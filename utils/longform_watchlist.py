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
      2. render_bbc_longform(card)['eligible'] == True (anchor >= MIN_ANCHOR_CHARS).
      3. render_bbc_longform(card)['proof_missing'] == False (has verifiable ISO date).

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

    # Find candidates: non-event, eligible, proof not missing
    candidates: list[tuple[int, float, object]] = []
    for card in all_cards:
        key = _card_key(card)
        if key in event_keys:
            continue
        lf = render_bbc_longform(card)
        if lf.get("eligible") and not lf.get("proof_missing"):
            anchor_chars = lf.get("anchor_chars", 0)
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

    # Proof coverage (should be 1.0: we only select proof_missing=False cards)
    wp_present = sum(
        1 for c in watchlist_cards
        if not getattr(c, _CACHE_ATTR, {}).get("proof_missing", True)
    )
    w_proof_ratio = round(wp_present / watchlist_selected, 3) if watchlist_selected else 1.0

    # Avg anchor chars
    w_anchor_sum = sum(
        getattr(c, _CACHE_ATTR, {}).get("anchor_chars", 0)
        for c in watchlist_cards
    )
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
