"""Offline unit tests for Longform Pool Expansion v1 (Watchlist/Developing track).

Tests:
  TW1  fill_to_min: when event_longform_count < min_daily_total and non-event
       pool has eligible cards, watchlist_selected fills up to min(needed, max_watchlist).
  TW2  never_promoted_to_event_slides: watchlist cards must NOT appear in ev_cards
       (exclusion by item_id / title-prefix key is watertight).
  TW3  meta_counts_self_consistent: write_watchlist_meta() produces consistent
       counts in exec_longform.meta.json.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from utils.longform_narrative import (
    MIN_ANCHOR_CHARS,
    render_bbc_longform,
    reset_stats,
    write_longform_meta,
    _CACHE_ATTR,
)
from utils.longform_watchlist import select_watchlist_cards, write_watchlist_meta


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------

@dataclass
class _StubCard:
    title_plain: str = ""
    what_happened: str = ""
    why_important: str = ""
    focus_action: str = ""
    metaphor: str = ""
    fact_check_confirmed: list = field(default_factory=list)
    fact_check_unverified: list = field(default_factory=list)
    evidence_lines: list = field(default_factory=list)
    technical_interpretation: str = ""
    derivable_effects: list = field(default_factory=list)
    speculative_effects: list = field(default_factory=list)
    observation_metrics: list = field(default_factory=list)
    action_items: list = field(default_factory=list)
    image_suggestions: list = field(default_factory=list)
    video_suggestions: list = field(default_factory=list)
    reading_suggestions: list = field(default_factory=list)
    source_url: str = ""
    category: str = ""
    signal_strength: float = 0.0
    final_score: float = 0.0
    source_name: str = ""
    item_id: str = ""
    is_valid_news: bool = True
    invalid_reason: str = ""
    invalid_cause: str = ""
    invalid_fix: str = ""
    published_at: str = ""
    published_at_parsed: str = ""
    collected_at: str = ""


_RICH_BODY = (
    "OpenAI released GPT-5 with 70B parameters achieving 94.5% on MMLU benchmarks. "
    "The model trained on 1.5 trillion tokens uses mixture-of-experts architecture. "
    "Microsoft Azure AI Studio integration is now generally available to enterprise customers. "
    "API pricing set at $0.003 per 1K tokens represents a 60% cost reduction. "
    "Anthropic Claude 4 shows competitive MMLU score of 93.2% on standard benchmarks. "
    "Meta Llama 4 with 405B parameters released as open-source weights today. "
    "xAI Grok 3 raised $500M Series B at $10B valuation; DeepSeek V3 ships 671B MoE. "
    "Google Gemini Ultra 2.0 achieves 97% on MATH benchmark evaluation suite. "
    "NVIDIA H200 GPU now available for AI training workloads at $30K per unit. "
    "Enterprise SDK v2.1.0 enables custom fine-tuning and deployment pipelines. "
) * 3  # ensure >> MIN_ANCHOR_CHARS


def _make_rich_card(item_id: str = "", title: str = "", score: float = 80.0) -> _StubCard:
    return _StubCard(
        item_id=item_id or f"rich_{title[:10]}",
        title_plain=title or f"Rich card {item_id}",
        what_happened="OpenAI released GPT-5 with 70B parameters. " + _RICH_BODY[:400],
        why_important="GPT-5 sets state-of-the-art across 50+ benchmarks. " + _RICH_BODY[400:700],
        technical_interpretation="Mixture-of-experts with 70B active params. " + _RICH_BODY[700:950],
        derivable_effects=[
            "Azure AI Studio integration brings GPT-5 to Copilot users by default.",
            "API pricing cut reduces enterprise AI costs by ~60%.",
        ],
        speculative_effects=["Competitors may cut prices within 30 days."],
        observation_metrics=["Watch Azure metrics in Q1 2026 earnings."],
        action_items=["TEST GPT-5 API by 2026-03-01."],
        evidence_lines=["arXiv:2402.10055 — GPT-5 technical report."],
        source_name="TestSource",
        category="AI Model Release",
        final_score=score,
    )


def _make_short_card(item_id: str = "short_001") -> _StubCard:
    return _StubCard(
        item_id=item_id,
        title_plain="Short article about AI",
        what_happened="Something happened.",
        why_important="It matters.",
        source_name="ShortSource",
    )


# ---------------------------------------------------------------------------
# TW1 — fill_to_min
# ---------------------------------------------------------------------------

def test_tw1_fill_to_min() -> None:
    """TW1: When event_longform_count < min_daily_total, watchlist fills the gap."""
    reset_stats()
    min_daily_total = 6
    max_watchlist = 8

    # Simulate 1 eligible event card (event_longform_count = 1)
    ev_card = _make_rich_card(item_id="ev_001", title="Event card 001")
    render_bbc_longform(ev_card)
    assert getattr(ev_card, _CACHE_ATTR, {}).get("eligible") is True

    event_cards = [ev_card]
    event_longform_count = 1  # confirmed from cache
    needed = min_daily_total - event_longform_count  # 5

    # Non-event pool: 8 rich (eligible) cards with distinct IDs
    pool = [_make_rich_card(item_id=f"pool_{i:03d}", title=f"Pool card {i:03d}") for i in range(8)]
    all_cards = event_cards + pool

    selected, candidates_total = select_watchlist_cards(
        all_cards, event_cards,
        min_daily_total=min_daily_total,
        max_watchlist=max_watchlist,
    )

    assert candidates_total >= needed, (
        f"Expected >= {needed} eligible non-event candidates; got {candidates_total}"
    )
    assert len(selected) == min(needed, max_watchlist, candidates_total), (
        f"Expected {min(needed, max_watchlist, candidates_total)} selected; got {len(selected)}"
    )
    # All selected must be eligible and proof_missing=False
    for card in selected:
        lf = getattr(card, _CACHE_ATTR, {}) or {}
        assert lf.get("eligible"), f"Selected card must be eligible: {card.item_id}"
        assert not lf.get("proof_missing"), f"Selected card must have proof: {card.item_id}"


# ---------------------------------------------------------------------------
# TW2 — never_promoted_to_event_slides
# ---------------------------------------------------------------------------

def test_tw2_never_promoted_to_event_slides() -> None:
    """TW2: Watchlist cards must NOT overlap with event_cards."""
    reset_stats()

    ev_cards = [_make_rich_card(item_id="ev_A", title="Event card A")]
    pool_non_event = [
        _make_rich_card(item_id="pool_B", title="Pool card B"),
        _make_rich_card(item_id="pool_C", title="Pool card C"),
        _make_short_card(item_id="short_D"),
    ]
    all_cards = ev_cards + pool_non_event

    for c in all_cards:
        render_bbc_longform(c)

    selected, _ = select_watchlist_cards(
        all_cards, ev_cards, min_daily_total=3, max_watchlist=5,
    )

    ev_ids = {(getattr(c, "item_id", "") or "") for c in ev_cards}
    ev_titles = {(getattr(c, "title_plain", "") or "")[:50] for c in ev_cards}

    for card in selected:
        cid = getattr(card, "item_id", "") or ""
        title = (getattr(card, "title_plain", "") or "")[:50]
        assert cid not in ev_ids or not cid, (
            f"Watchlist card item_id '{cid}' must not match any event card"
        )
        assert title not in ev_titles or not title, (
            f"Watchlist card title prefix '{title}' must not match any event card"
        )


# ---------------------------------------------------------------------------
# TW3 — meta_counts_self_consistent
# ---------------------------------------------------------------------------

def test_tw3_meta_counts_self_consistent() -> None:
    """TW3: write_watchlist_meta() produces self-consistent counts in meta JSON."""
    import tempfile

    reset_stats()
    min_daily_total = 6
    max_watchlist = 8

    # 2 rich event cards + 5 rich non-event cards + 1 short non-event card
    ev_cards = [
        _make_rich_card(item_id="ev_001", title="Event card 001"),
        _make_rich_card(item_id="ev_002", title="Event card 002"),
    ]
    non_event_pool = [
        _make_rich_card(item_id=f"pool_{i:03d}", title=f"Pool card {i:03d}")
        for i in range(5)
    ] + [_make_short_card(item_id="short_999")]
    all_cards = ev_cards + non_event_pool

    # Run longform on all (as pipeline would)
    for c in all_cards:
        render_bbc_longform(c)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Step 1: write event longform meta
        write_longform_meta(event_cards=ev_cards, outdir=tmpdir)

        # Step 2: select and write watchlist meta
        selected, candidates_total = select_watchlist_cards(
            all_cards, ev_cards,
            min_daily_total=min_daily_total,
            max_watchlist=max_watchlist,
        )
        write_watchlist_meta(
            event_cards=ev_cards,
            watchlist_cards=selected,
            candidates_total=candidates_total,
            min_daily_total=min_daily_total,
            outdir=tmpdir,
        )

        meta_path = Path(tmpdir) / "exec_longform.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    ev_lf_count     = meta["event_longform_count"]
    wl_selected     = meta["watchlist_longform_selected"]
    wl_candidates   = meta["watchlist_longform_candidates"]
    daily_total     = meta["longform_daily_total"]
    wl_proof_ratio  = meta["watchlist_proof_coverage_ratio"]
    min_target      = meta["longform_min_daily_total"]

    # event_longform_count == number of eligible event cards
    ev_eligible = sum(1 for c in ev_cards if getattr(c, _CACHE_ATTR, {}).get("eligible"))
    assert ev_lf_count == ev_eligible, (
        f"event_longform_count={ev_lf_count} != eligible_event_cards={ev_eligible}"
    )

    # longform_daily_total == event_longform_count + watchlist_longform_selected
    assert daily_total == ev_lf_count + wl_selected, (
        f"daily_total={daily_total} != {ev_lf_count} + {wl_selected}"
    )

    # watchlist_proof_coverage_ratio must be 1.0 (all selected have proof_missing=False)
    assert wl_proof_ratio == 1.0, (
        f"watchlist_proof_coverage_ratio={wl_proof_ratio} must be 1.0 "
        f"(only proof_missing=False cards selected)"
    )

    # min_daily_total recorded correctly
    assert min_target == min_daily_total, (
        f"longform_min_daily_total={min_target} != {min_daily_total}"
    )

    # watchlist_longform_candidates >= watchlist_longform_selected
    assert wl_candidates >= wl_selected, (
        f"candidates({wl_candidates}) must be >= selected({wl_selected})"
    )
