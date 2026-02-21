"""utils/longform_watchlist.py — Longform Pool Expansion v1 (Watchlist/Developing track).

Stdlib-only. No new pip deps.

Selects non-event cards that have enough anchor text (>= MIN_ANCHOR_CHARS) to be
presented as Developing / Watchlist longform summaries, topping up to
LONGFORM_MIN_DAILY_TOTAL when the event_longform_count falls short.

Public API:
    select_watchlist_cards(all_cards, event_cards, min_daily_total=6, max_watchlist=8)
        -> tuple[list, int]   (selected_cards, candidates_total)
    write_watchlist_meta(event_cards, watchlist_cards, candidates_total,
                         min_daily_total=6, outdir=None) -> None
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas.education_models import EduNewsCard

# Shared cache attribute name (must match longform_narrative._CACHE_ATTR)
_CACHE_ATTR = "_longform_v1_cache"

# Watchlist-specific payload cache (lower anchor threshold)
_WATCHLIST_PAYLOAD_ATTR = "_watchlist_longform_payload"

# Lower anchor threshold for watchlist candidates (vs 1200 for event longform)
LONGFORM_WATCHLIST_MIN_ANCHOR_CHARS: int = 600


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _event_card_keys(event_cards: list) -> set[str]:
    """Return set of item_id / title-prefix keys used to exclude event cards."""
    keys: set[str] = set()
    for c in event_cards:
        cid = (getattr(c, "item_id", "") or "").strip()
        title = (getattr(c, "title_plain", "") or "")[:50].strip()
        if cid:
            keys.add(cid)
        if title:
            keys.add(title)
    return keys


def _card_key(card) -> str:
    cid = (getattr(card, "item_id", "") or "").strip()
    title = (getattr(card, "title_plain", "") or "")[:50].strip()
    return cid or title


def _pick_watchlist_anchor(card, min_chars: int = LONGFORM_WATCHLIST_MIN_ANCHOR_CHARS) -> str | None:
    """Combine available text fields; return combined if >= min_chars, else None."""
    parts: list[str] = []
    for attr in (
        "what_happened", "why_important", "technical_interpretation",
        "title_plain", "focus_action", "metaphor",
    ):
        val = str(getattr(card, attr, "") or "").strip()
        if val:
            parts.append(val)
    # Also include list fields (evidence, derivable_effects, etc.)
    for attr in ("evidence_lines", "fact_check_confirmed", "derivable_effects"):
        lst = getattr(card, attr, None) or []
        for item in lst:
            val = str(item or "").strip()
            if val:
                parts.append(val)
    combined = " ".join(parts)
    return combined if len(combined) >= min_chars else None


def _zh_ratio_simple(text: str) -> float:
    """Quick CJK ratio check without importing newsroom module."""
    if not text:
        return 0.0
    zh = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return zh / max(1, len(text))


def _build_watchlist_payload(card) -> dict:
    """Build three-line watchlist payload: {what, why, proof_line, anchor_chars, eligible, proof_missing}.

    Uses canonical newsroom rewrite (Iteration 3) for ZH-dominant output.
    Applies exec_sanitizer to all text output.
    """
    try:
        from utils.exec_sanitizer import sanitize_exec_text
    except Exception:
        def sanitize_exec_text(t: str) -> str:  # type: ignore[misc]
            return t

    try:
        from utils.longform_narrative import _make_date_proof_line
        proof_line = _make_date_proof_line(card)
    except Exception:
        src = str(getattr(card, "source_name", "") or "未知來源").strip()
        pub = str(getattr(card, "published_at_parsed", "") or getattr(card, "published_at", "") or "").strip()[:10]
        proof_line = f"證據：來源：{src}（{pub}）" if pub else f"證據：來源：{src}"

    # proof_missing = True if no ISO date in proof_line
    import re as _re
    proof_missing = not bool(_re.search(r"\d{4}-\d{2}-\d{2}", proof_line))

    # ── Newsroom ZH rewrite for what/why (Iteration 3) ──────────────────────
    what_raw = str(getattr(card, "what_happened", "") or "").strip()
    if not what_raw:
        what_raw = str(getattr(card, "title_plain", "") or "").strip()
    why_raw = str(getattr(card, "why_important", "") or "").strip()
    if not why_raw:
        why_raw = str(getattr(card, "technical_interpretation", "") or "").strip()

    _ZH_MIN_WL = 0.20  # Watchlist ZH threshold

    try:
        from utils.newsroom_zh_rewrite import (
            rewrite_news_lead_v2 as _nzh_wl_lead,
            rewrite_news_impact_v2 as _nzh_wl_impact,
            zh_ratio as _nzh_wl_ratio,
        )
        # Build minimal context for rewriter
        _wl_bucket = "business"
        try:
            from utils.topic_router import route_topic as _rt_wl
            _ch = (_rt_wl(card).get("channel", "") or "").lower()
            if _ch in ("product", "tech", "business", "dev"):
                _wl_bucket = _ch
        except Exception:
            pass

        _wl_ctx = {
            "title":   str(getattr(card, "title_plain", "") or "").strip(),
            "date":    "",
            "subject": "",
            "bucket":  _wl_bucket,
            "source":  str(getattr(card, "source_name", "") or "").strip(),
            "what_happened":  what_raw,
            "why_important":  why_raw,
            "action_items":   list(getattr(card, "action_items", None) or []),
            "speculative_effects": list(getattr(card, "speculative_effects", None) or []),
            "derivable_effects":   list(getattr(card, "derivable_effects", None) or []),
        }
        # Extract date for context
        import re as _re2
        for _attr in ("published_at", "published_at_parsed", "collected_at"):
            _val = getattr(card, _attr, None)
            if _val:
                _dm = _re2.search(r"\d{4}-\d{2}-\d{2}", str(_val))
                if _dm:
                    _wl_ctx["date"] = _dm.group(0)
                    break

        # Extract anchors for v2 injection (Iteration 4)
        _wl_anchors: list[str] = []
        _wl_primary: "str | None" = None
        try:
            from utils.news_anchor import (
                extract_anchors_from_card as _wl_eafc,
                pick_primary_anchor as _wl_ppa,
            )
            _wl_ar = _wl_eafc(card)
            _wl_anchors = _wl_ar.get("anchors", []) or []
            _wl_at      = _wl_ar.get("anchor_types", {}) or {}
            _wl_primary = _wl_ppa(_wl_anchors, _wl_at) if _wl_anchors else None
        except Exception:
            pass

        what_rewritten = _nzh_wl_lead(
            what_raw, _wl_ctx,
            anchors=_wl_anchors, primary_anchor=_wl_primary,
        )
        why_rewritten = _nzh_wl_impact(
            why_raw, _wl_ctx,
            anchors=_wl_anchors, primary_anchor=_wl_primary,
        )

        # Use rewritten if zh_ratio improved enough
        what = what_rewritten if _nzh_wl_ratio(what_rewritten) >= _ZH_MIN_WL else what_raw
        why = why_rewritten if _nzh_wl_ratio(why_rewritten) >= _ZH_MIN_WL else why_raw

    except Exception:
        # Fallback: use raw fields
        what = what_raw
        why = why_raw

    # Final sanitize
    what = sanitize_exec_text(what[:300])
    why = sanitize_exec_text(why[:300])

    anchor = _pick_watchlist_anchor(card)
    anchor_chars = len(anchor) if anchor else 0
    eligible = anchor_chars >= LONGFORM_WATCHLIST_MIN_ANCHOR_CHARS and bool(what)

    return {
        "what": what,
        "why": why,
        "proof_line": proof_line,
        "anchor_chars": anchor_chars,
        "eligible": eligible,
        "proof_missing": proof_missing,
        "watchlist": True,
    }


# ---------------------------------------------------------------------------
# select_watchlist_cards
# ---------------------------------------------------------------------------

def select_watchlist_cards(
    all_cards: list,
    event_cards: list,
    min_daily_total: int = 6,
    max_watchlist: int = 8,
) -> tuple[list, int]:
    """Select non-event cards eligible for the Watchlist/Developing longform track.

    Selection criteria (applied in order):
      1. Not an event card (excluded by item_id or title-prefix match).
      2. Has anchor text >= LONGFORM_WATCHLIST_MIN_ANCHOR_CHARS (600 chars).
         First tries render_bbc_longform (1200 char threshold); falls back to
         watchlist-specific lower threshold if that fails.
      3. proof_missing == False (has verifiable ISO date).

    Sorted by anchor_chars DESC, then final_score DESC.

    Returns:
        (selected_cards, candidates_total)
        len(selected_cards) == min(needed, max_watchlist, candidates_total)
        where needed = max(0, min_daily_total - event_longform_count)
    """
    from utils.longform_narrative import render_bbc_longform

    event_keys = _event_card_keys(event_cards)

    # Count event cards that already have longform (eligible=True in cache)
    event_longform_count = sum(
        1 for c in event_cards
        if getattr(c, _CACHE_ATTR, None) and
        getattr(c, _CACHE_ATTR, {}).get("eligible", False)
    )

    needed = max(0, min_daily_total - event_longform_count)

    # Find candidates: non-event, eligible (watchlist threshold), proof not missing
    candidates: list[tuple[int, float, object]] = []
    for card in all_cards:
        key = _card_key(card)
        if key in event_keys:
            continue

        # Skip cards with no concrete anchors (Iteration 4: anchor-missing filter)
        try:
            from utils.news_anchor import extract_anchors_from_card as _wl_sel_eafc
            _wl_sel_ar = _wl_sel_eafc(card)
            if not _wl_sel_ar.get("has_anchor", False):
                continue
        except Exception:
            pass  # if news_anchor unavailable, don't skip

        # Try full BBC longform first (1200 char threshold)
        lf = render_bbc_longform(card)
        if lf.get("eligible") and not lf.get("proof_missing"):
            # Store watchlist payload from full longform data
            wl_payload = {
                "what": lf.get("bg") or lf.get("what_is") or "",
                "why": lf.get("why") or "",
                "proof_line": lf.get("proof_line") or "",
                "anchor_chars": lf.get("anchor_chars", 0),
                "eligible": True,
                "proof_missing": False,
                "watchlist": True,
            }
            try:
                from utils.exec_sanitizer import sanitize_exec_text
                wl_payload["what"] = sanitize_exec_text(wl_payload["what"])
                wl_payload["why"] = sanitize_exec_text(wl_payload["why"])
            except Exception:
                pass
            setattr(card, _WATCHLIST_PAYLOAD_ATTR, wl_payload)
            anchor_chars = lf.get("anchor_chars", 0)
            score = float(getattr(card, "final_score", 0) or 0)
            candidates.append((anchor_chars, score, card))
            continue

        # Fall back to watchlist-specific lower threshold (600 chars)
        wl = _build_watchlist_payload(card)
        if wl.get("eligible") and not wl.get("proof_missing"):
            setattr(card, _WATCHLIST_PAYLOAD_ATTR, wl)
            anchor_chars = wl.get("anchor_chars", 0)
            score = float(getattr(card, "final_score", 0) or 0)
            candidates.append((anchor_chars, score, card))

    candidates.sort(key=lambda x: (-x[0], -x[1]))
    candidates_total = len(candidates)

    take = min(needed, max_watchlist, candidates_total)
    selected = [c for _, _, c in candidates[:take]]

    return selected, candidates_total


# ---------------------------------------------------------------------------
# write_watchlist_meta
# ---------------------------------------------------------------------------

def write_watchlist_meta(
    event_cards: list,
    watchlist_cards: list,
    candidates_total: int,
    min_daily_total: int = 6,
    outdir: "str | Path | None" = None,
) -> None:
    """Append watchlist fields to outputs/exec_longform.meta.json.

    Merges the following keys into the existing meta file (written by
    write_longform_meta earlier in the same pipeline run):

        longform_min_daily_total       int
        event_longform_count           int
        watchlist_longform_candidates  int
        watchlist_longform_selected    int
        longform_daily_total           int
        watchlist_selected_ids_top10   list[str]
        watchlist_sources_share_top3   list[{source, count}]
        watchlist_avg_anchor_chars     float
        watchlist_proof_coverage_ratio float
    """
    if outdir is None:
        outdir = Path(__file__).parent.parent / "outputs"
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    dest = out / "exec_longform.meta.json"

    # Load existing meta (created by write_longform_meta earlier)
    if dest.exists():
        try:
            meta = json.loads(dest.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    else:
        meta = {}

    # Event longform count (eligible cards with cached result)
    event_longform_count = sum(
        1 for c in event_cards
        if getattr(c, _CACHE_ATTR, None) and
        getattr(c, _CACHE_ATTR, {}).get("eligible", False)
    )

    watchlist_selected = len(watchlist_cards)
    longform_daily_total = event_longform_count + watchlist_selected

    # Source distribution top-3
    source_counts: dict[str, int] = {}
    for card in watchlist_cards:
        src = (getattr(card, "source_name", "") or "unknown").strip()
        source_counts[src] = source_counts.get(src, 0) + 1
    top3 = sorted(source_counts.items(), key=lambda x: -x[1])[:3]

    # Proof coverage — check watchlist payload first, then longform cache
    def _card_proof_missing(card) -> bool:
        wl = getattr(card, _WATCHLIST_PAYLOAD_ATTR, None)
        if wl is not None:
            return wl.get("proof_missing", True)
        return getattr(card, _CACHE_ATTR, {}).get("proof_missing", True)

    wp_present = sum(1 for c in watchlist_cards if not _card_proof_missing(c))
    w_proof_ratio = round(wp_present / watchlist_selected, 3) if watchlist_selected else 1.0

    # Avg anchor chars — prefer watchlist payload anchor_chars
    def _card_anchor_chars(card) -> int:
        wl = getattr(card, _WATCHLIST_PAYLOAD_ATTR, None)
        if wl is not None:
            return wl.get("anchor_chars", 0)
        return getattr(card, _CACHE_ATTR, {}).get("anchor_chars", 0)

    w_anchor_sum = sum(_card_anchor_chars(c) for c in watchlist_cards)
    w_avg_anchor = round(w_anchor_sum / watchlist_selected, 1) if watchlist_selected else 0.0

    # Selected IDs top-10
    w_ids: list[str] = []
    for c in watchlist_cards[:10]:
        cid = (getattr(c, "item_id", "") or (getattr(c, "title_plain", "") or "")[:30])
        w_ids.append(str(cid))

    meta.update({
        "longform_min_daily_total": min_daily_total,
        "event_longform_count": event_longform_count,
        "watchlist_longform_candidates": candidates_total,
        "watchlist_longform_selected": watchlist_selected,
        "longform_daily_total": longform_daily_total,
        "watchlist_selected_ids_top10": w_ids,
        "watchlist_sources_share_top3": [{"source": k, "count": v} for k, v in top3],
        "watchlist_avg_anchor_chars": w_avg_anchor,
        "watchlist_proof_coverage_ratio": w_proof_ratio,
    })
    meta["generated_at"] = datetime.now(timezone.utc).isoformat()

    dest.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
