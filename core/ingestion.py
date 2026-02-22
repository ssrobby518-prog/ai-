"""Z1 – Ingestion & Preprocessing.

Fetch RSS -> clean -> normalize -> dedup -> filter -> batch.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import feedparser
import requests
from config import settings
from core.content_gate import apply_split_content_gate
from langdetect import LangDetectException, detect
from rapidfuzz import fuzz
from schemas.models import RawItem
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from utils.hashing import url_hash
from utils.logger import get_logger
from utils.text_clean import normalize_whitespace, strip_html

# ---------------------------------------------------------------------------
# RSS fetching with retries
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((requests.RequestException, ConnectionError)),
    reraise=True,
)
def _fetch_feed_text(url: str, timeout: int = 30) -> str:
    """Download raw RSS/Atom XML with retries."""
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "AI-Intel-Scraper/1.0"})
    resp.raise_for_status()
    return resp.text


def fetch_feed(feed_cfg: dict) -> list[RawItem]:
    """Fetch a single RSS feed and return normalized RawItems."""
    log = get_logger()
    url = feed_cfg["url"]
    name = feed_cfg.get("name", url)
    lang = feed_cfg.get("lang", "en")
    category = feed_cfg.get("category", "general")

    t0 = time.time()
    try:
        xml = _fetch_feed_text(url)
    except Exception as exc:
        log.error("Failed to fetch feed %s: %s", name, exc)
        return []

    parsed = feedparser.parse(xml)
    items: list[RawItem] = []

    for entry in parsed.entries:
        link = entry.get("link", "")
        if not link:
            continue

        # Published time
        published = ""
        for time_field in ("published_parsed", "updated_parsed"):
            tp = entry.get(time_field)
            if tp:
                try:
                    dt = datetime(
                        tp.tm_year,
                        tp.tm_mon,
                        tp.tm_mday,
                        tp.tm_hour,
                        tp.tm_min,
                        tp.tm_sec,
                        tzinfo=UTC,
                    )
                    published = dt.isoformat()
                except Exception:
                    pass
                break

        # Body: prefer content, then summary
        raw_body = ""
        if entry.get("content"):
            raw_body = entry.content[0].get("value", "")
        elif entry.get("summary"):
            raw_body = entry.summary

        body = normalize_whitespace(strip_html(raw_body))
        title = normalize_whitespace(strip_html(entry.get("title", "")))

        items.append(
            RawItem(
                item_id=url_hash(link),
                title=title,
                url=link,
                body=body,
                published_at=published,
                source_name=name,
                source_category=category,
                lang=lang,
            )
        )

    elapsed = time.time() - t0
    log.info("Fetched feed %-15s | %d entries | %.2fs", name, len(items), elapsed)
    return items


def fetch_all_feeds() -> list[RawItem]:
    """Fetch all configured feeds and combine results."""
    from utils.article_fetch import enrich_items_async
    from utils.metrics import get_collector

    rss_items: list[RawItem] = []
    rss_success = 0
    rss_failed = 0
    for feed_cfg in settings.RSS_FEEDS:
        items = fetch_feed(feed_cfg)
        rss_items.extend(items)
        if items:
            rss_success += 1
        else:
            rss_failed += 1

    plugin_items: list[RawItem] = []
    plugin_stats = {
        "sources_total": 0,
        "sources_success": 0,
        "sources_failed": 0,
    }
    try:
        from core.sources import fetch_all_sources_with_stats

        plugin_items, plugin_stats = fetch_all_sources_with_stats()
    except Exception:
        # Keep pipeline resilient if plugin loading fails.
        plugin_items = []

    all_items = rss_items + plugin_items

    collector = get_collector()
    collector.sources_total = len(settings.RSS_FEEDS) + int(plugin_stats.get("sources_total", 0))
    collector.sources_success = rss_success + int(plugin_stats.get("sources_success", 0))
    collector.sources_failed = rss_failed + int(plugin_stats.get("sources_failed", 0))
    return enrich_items_async(all_items, stats=collector.enrich_stats)


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def dedup_items(items: list[RawItem], existing_ids: set[str] | None = None) -> list[RawItem]:
    """Remove duplicates by URL hash + fuzzy title similarity."""
    log = get_logger()
    existing_ids = existing_ids or set()
    seen_ids: set[str] = set(existing_ids)
    seen_titles: list[str] = []
    result: list[RawItem] = []

    for item in items:
        # Exact URL dedup
        if item.item_id in seen_ids:
            continue
        # Fuzzy title dedup (threshold 85)
        is_dup = False
        for prev_title in seen_titles:
            if fuzz.ratio(item.title, prev_title) > 85:
                is_dup = True
                break
        if is_dup:
            continue

        seen_ids.add(item.item_id)
        seen_titles.append(item.title)
        result.append(item)

    removed = len(items) - len(result)
    if removed:
        log.info("Dedup removed %d items, %d remaining", removed, len(result))
    return result


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def _detect_lang(text: str) -> str:
    """Detect language, return empty string on failure."""
    try:
        return detect(text)
    except LangDetectException:
        return ""


@dataclass
class FilterSummary:
    """Per-reason breakdown of items dropped by filter_items()."""

    input_count: int = 0
    kept_count: int = 0
    dropped_by_reason: dict[str, int] = field(default_factory=dict)
    gate_stats: dict[str, int | list[tuple[str, int]] | list[tuple[str, str, int]]] = field(default_factory=dict)
    signal_pool: list[RawItem] = field(default_factory=list)
    # reasons: too_old, lang_not_allowed, keyword_mismatch, body_too_short,
    #          content_too_short, insufficient_sentences, rejected_keyword:*


def filter_items(items: list[RawItem]) -> tuple[list[RawItem], FilterSummary]:
    """Apply time, language, keyword, and length filters.

    Returns (filtered_items, summary) where summary contains per-reason drop counts.
    """
    log = get_logger()
    cutoff = datetime.now(UTC) - timedelta(hours=settings.NEWER_THAN_HOURS)
    result: list[RawItem] = []
    gate_candidates: list[RawItem] = []
    summary = FilterSummary(input_count=len(items))

    for item in items:
        # Time filter
        if item.published_at:
            try:
                pub_dt = datetime.fromisoformat(item.published_at)
                if pub_dt < cutoff:
                    summary.dropped_by_reason["too_old"] = summary.dropped_by_reason.get("too_old", 0) + 1
                    continue
            except ValueError:
                pass  # keep items with unparseable dates

        # Language filter
        if settings.ALLOW_LANG:
            detected = _detect_lang(item.title + " " + item.body[:200])
            if detected and detected not in settings.ALLOW_LANG:
                summary.dropped_by_reason["lang_not_allowed"] = summary.dropped_by_reason.get("lang_not_allowed", 0) + 1
                continue

        # Keyword filter (if configured, at least one keyword must appear)
        if settings.KEYWORD_FILTER:
            combined = (item.title + " " + item.body).lower()
            if not any(kw.lower() in combined for kw in settings.KEYWORD_FILTER):
                summary.dropped_by_reason["keyword_mismatch"] = summary.dropped_by_reason.get("keyword_mismatch", 0) + 1
                continue

        # Min body length — G1 dual threshold (Iter 6.5):
        # social/optional platforms require a longer body to avoid low-quality snippets;
        # main-pool sources accept shorter release notes / abstracts (>= 220 chars).
        _z0_platform = getattr(item, "z0_platform", "") or ""
        _is_social = _z0_platform in settings.SOCIAL_OPTIONAL_PLATFORMS
        _min_body = settings.MIN_BODY_LENGTH_SOCIAL if _is_social else settings.MIN_BODY_LENGTH_MAIN
        if len(item.body) < _min_body:
            summary.dropped_by_reason["body_too_short"] = summary.dropped_by_reason.get("body_too_short", 0) + 1
            continue

        gate_candidates.append(item)

    # Split content gates (event vs signal) after hard quality filters.
    event_candidates, signal_pool, _rejected_map, gate_stats = apply_split_content_gate(
        gate_candidates,
        event_level=(
            settings.EVENT_GATE_MIN_LEN,
            settings.EVENT_GATE_MIN_SENTENCES,
        ),
        signal_level=(
            settings.SIGNAL_GATE_MIN_LEN,
            settings.SIGNAL_GATE_MIN_SENTENCES,
        ),
    )
    result.extend(event_candidates)
    _after_filter_raw = len(result)  # count before G4 top-up (raw gate output)

    # G4 fallback (Iter 6.5): top-up result to FILTER_FALLBACK_N when below threshold.
    # Fires whenever result < fallback_n (not only when result == 0) so a run with
    # e.g. 5 event candidates still gets promoted to 6 from signal_pool.
    # Hard-UI-token items are skipped to keep garbage out of the deck.
    _fallback_n = getattr(settings, "FILTER_FALLBACK_N", 6)
    if len(result) < _fallback_n and signal_pool:
        _existing_ids = {id(x) for x in result}
        _hard_ui_toks = ("enable javascript", "javascript is required", "javascript required")
        _pool_sorted = sorted(
            signal_pool,
            key=lambda x: (getattr(x, "z0_frontier_score", 0), getattr(x, "density_score", 0)),
            reverse=True,
        )
        _needed = _fallback_n - len(result)
        _added = 0
        for _fi in _pool_sorted:
            if _added >= _needed:
                break
            if id(_fi) in _existing_ids:
                continue
            _fi_body = (getattr(_fi, "body", "") or "").lower()
            if any(tok in _fi_body for tok in _hard_ui_toks):
                continue
            try:
                setattr(_fi, "event_gate_pass", True)
                setattr(_fi, "backfill_pass", True)
                setattr(_fi, "gate_level", "g4_signal_fallback")
            except Exception:
                pass
            result.append(_fi)
            _existing_ids.add(id(_fi))
            _added += 1
        log.info(
            "G4_FALLBACK: raw=%d needed=%d added=%d total=%d signal_pool=%d",
            _after_filter_raw, _needed, _added, len(result), len(signal_pool),
        )

    summary.signal_pool = signal_pool

    for reason, count in gate_stats.rejected_by_reason.items():
        summary.dropped_by_reason[reason] = summary.dropped_by_reason.get(reason, 0) + count

    summary.kept_count = len(result)
    dropped_total = summary.input_count - summary.kept_count
    summary.gate_stats = {
        "total": gate_stats.total,
        "event_gate_pass_total": gate_stats.event_gate_pass_total,
        "signal_gate_pass_total": gate_stats.signal_gate_pass_total,
        "hard_pass_total": gate_stats.event_gate_pass_total,
        "soft_pass_total": max(gate_stats.signal_gate_pass_total - gate_stats.event_gate_pass_total, 0),
        "gate_pass_total": gate_stats.event_gate_pass_total,
        "passed_strict": gate_stats.event_gate_pass_total,
        "passed_relaxed": max(gate_stats.signal_gate_pass_total - gate_stats.event_gate_pass_total, 0),
        "passed_density_soft": 0,
        "gate_reject_total": gate_stats.rejected_total,
        "rejected_total": gate_stats.rejected_total,
        "density_score_top5": [],
        "rejected_reason_top": gate_stats.rejected_reason_top,
        "after_filter_total": len(result),
    }
    log.info("Filters: %d -> %d items", len(items), len(result))
    log.info(
        "ContentGate fetched_total=%d event_gate_pass_total=%d signal_gate_pass_total=%d rejected=%d reasons_top=%s",
        len(items),
        gate_stats.event_gate_pass_total,
        gate_stats.signal_gate_pass_total,
        gate_stats.rejected_total,
        gate_stats.rejected_reason_top,
    )
    log.info(
        "FILTER_SUMMARY kept=%d dropped_total=%d reasons=%s",
        summary.kept_count,
        dropped_total,
        summary.dropped_by_reason,
    )

    # Write filter_summary.meta.json for NO_ZERO_DAY gate in verify_online.ps1.
    try:
        import json as _json
        _kept_total = summary.kept_count  # post-G4 final count
        _fs_meta = {
            "after_dedupe_total": summary.input_count,
            "after_filter_total_raw": _after_filter_raw,   # before G4 top-up
            "kept_total": _kept_total,                     # effective: after G4
            "after_filter_total": _kept_total,             # alias → gate reads this
            "kept_count": _kept_total,
            "event_gate_pass_total": gate_stats.event_gate_pass_total,
            "signal_gate_pass_total": gate_stats.signal_gate_pass_total,
            "dropped_by_reason": summary.dropped_by_reason,
        }
        _fs_path = settings.PROJECT_ROOT / "outputs" / "filter_summary.meta.json"
        _fs_path.parent.mkdir(parents=True, exist_ok=True)
        _fs_path.write_text(_json.dumps(_fs_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as _e:
        log.warning("filter_summary.meta.json write failed: %s", _e)

    return result, summary


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------


def fetch_from_plugins() -> list[RawItem]:
    """Fetch items from all registered source plugins (auto-discovered)."""
    from core.sources import fetch_all_sources

    log = get_logger()
    items = fetch_all_sources()
    log.info("Plugins returned %d total items", len(items))
    return items


def batch_items(items: list[RawItem], batch_size: int | None = None) -> Generator[list[RawItem], None, None]:
    """Yield successive batches of items."""
    bs = batch_size or settings.BATCH_SIZE
    for i in range(0, len(items), bs):
        yield items[i : i + bs]
