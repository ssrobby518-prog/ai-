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

    all_items: list[RawItem] = []
    for feed_cfg in settings.RSS_FEEDS:
        all_items.extend(fetch_feed(feed_cfg))

    collector = get_collector()
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
    # reasons: too_old, lang_not_allowed, keyword_mismatch, body_too_short


def filter_items(items: list[RawItem]) -> tuple[list[RawItem], FilterSummary]:
    """Apply time, language, keyword, and length filters.

    Returns (filtered_items, summary) where summary contains per-reason drop counts.
    """
    log = get_logger()
    cutoff = datetime.now(UTC) - timedelta(hours=settings.NEWER_THAN_HOURS)
    result: list[RawItem] = []
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

        # Min body length
        if len(item.body) < settings.MIN_BODY_LENGTH:
            summary.dropped_by_reason["body_too_short"] = summary.dropped_by_reason.get("body_too_short", 0) + 1
            continue

        result.append(item)

    summary.kept_count = len(result)
    dropped_total = summary.input_count - summary.kept_count
    log.info("Filters: %d -> %d items", len(items), len(result))
    log.info(
        "FILTER_SUMMARY kept=%d dropped_total=%d reasons=%s",
        summary.kept_count,
        dropped_total,
        summary.dropped_by_reason,
    )
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
