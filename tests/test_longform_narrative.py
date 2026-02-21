"""Offline unit tests for Longform Narrative v1 (BBC-style Anti-Fragment).

Tests verify:
  T1  Short card (< MIN_ANCHOR_CHARS) → pick_anchor_text returns None (ineligible)
  T2  Rich card (>= MIN_ANCHOR_CHARS) → render_bbc_longform returns all 5 sections
  T3  Card with proof token → proof_line is non-empty, proof_missing = False
  T4  render_bbc_longform caches result; double-call does NOT double-count stats
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from utils.longform_narrative import (
    MIN_ANCHOR_CHARS,
    build_sections,
    extract_key_sentences,
    pick_anchor_text,
    render_bbc_longform,
    reset_stats,
    _stats,
)


# ---------------------------------------------------------------------------
# Minimal stub for EduNewsCard (avoids import dependency on full schemas)
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
    # Dynamic metadata (mirrors raw item fields used by longform_narrative)
    published_at: str = ""
    published_at_parsed: str = ""
    collected_at: str = ""


def _short_card() -> _StubCard:
    """Card with text well below MIN_ANCHOR_CHARS."""
    return _StubCard(
        title_plain="Short article about AI",
        what_happened="Something happened.",
        why_important="It matters.",
    )


def _rich_card() -> _StubCard:
    """Card with text well above MIN_ANCHOR_CHARS, including a proof token."""
    long_body = (
        "OpenAI announced GPT-5 with 70B parameters achieving 94.5% on MMLU. "
        "The model was trained on 1.5 trillion tokens and achieves state-of-the-art "
        "performance across reasoning, coding, and multimodal tasks. "
        "Microsoft has integrated GPT-5 into Azure AI Studio and Copilot. "
        "The API is now generally available at $0.003 per 1K tokens. "
        "Enterprise customers can deploy via SDK v2.1.0 starting today. "
        "Anthropic Claude 4 benchmarks show competitive MMLU of 93.2%. "
        "Meta released Llama 4 with 405B parameters open-source weights. "
        "The model outperforms previous versions on HumanEval coding tasks. "
        "xAI Grok 3 raised $500M in Series B funding at $10B valuation. "
        "DeepSeek V3 open-source model with 671B MoE params shipped today. "
        "Google Gemini Ultra 2.0 achieves 97% on MATH benchmark. "
        "NVIDIA H200 GPU now available for AI training at $30K per unit. "
    ) * 4  # Repeat to guarantee > 1200 chars

    return _StubCard(
        title_plain="GPT-5 70B achieves 94.5% MMLU, API now GA at $0.003/1K tokens",
        what_happened=(
            "OpenAI released GPT-5 with 70B parameters. "
            "The model achieves 94.5% on MMLU and is now generally available. "
            + long_body[:400]
        ),
        why_important=(
            "GPT-5 sets a new state-of-the-art across 50+ benchmarks. "
            "Enterprise adoption will accelerate as pricing drops to $0.003/1K tokens. "
            + long_body[400:700]
        ),
        technical_interpretation=(
            "Mixture-of-experts architecture with 70B active params from 200B total. "
            "Trained on 1.5 trillion tokens with RLHF fine-tuning. "
            + long_body[700:950]
        ),
        derivable_effects=[
            "Azure AI Studio integration means Microsoft Copilot users get GPT-5 by default.",
            "API pricing cut will reduce enterprise AI costs by ~60%.",
        ],
        speculative_effects=[
            "Competitors may be forced to cut prices within 30 days.",
            "Some safety researchers question the rapid deployment timeline.",
        ],
        observation_metrics=[
            "Watch Azure usage metrics in Q1 2026 earnings call.",
            "Monitor Anthropic pricing response within 7 days.",
        ],
        action_items=[
            "TEST GPT-5 API against current Claude 3.5 Sonnet workflow by 2026-03-01.",
            "WATCH NVIDIA H200 demand spike in next earnings report.",
        ],
        evidence_lines=[
            "arXiv:2402.10055 — GPT-5 technical report.",
            "OpenAI blog post confirms $0.003/1K token pricing.",
        ],
        category="AI Model Release",
        final_score=95.0,
    )


# ---------------------------------------------------------------------------
# T1 — Short card is ineligible
# ---------------------------------------------------------------------------

def test_t1_short_card_ineligible() -> None:
    """T1: Card with text < MIN_ANCHOR_CHARS → pick_anchor_text returns None."""
    card = _short_card()
    result = pick_anchor_text(card)
    assert result is None, (
        f"Expected None for short card (combined < {MIN_ANCHOR_CHARS} chars); got {len(result or '')} chars"
    )


# ---------------------------------------------------------------------------
# T2 — Rich card returns all 5 BBC sections
# ---------------------------------------------------------------------------

def test_t2_rich_card_all_sections_present() -> None:
    """T2: Rich card → render_bbc_longform returns eligible=True + all 5 sections."""
    reset_stats()
    card = _rich_card()
    result = render_bbc_longform(card)

    assert result["eligible"] is True, "Expected eligible=True for rich card"
    assert result["anchor_chars"] >= MIN_ANCHOR_CHARS, (
        f"Expected anchor_chars >= {MIN_ANCHOR_CHARS}; got {result['anchor_chars']}"
    )

    for section in ("bg", "what_is", "why", "risks", "next"):
        assert isinstance(result[section], str), f"Section '{section}' must be a str"
        assert len(result[section]) > 0, f"Section '{section}' must be non-empty"

    # Sections must NOT contain fallback placeholder for eligible cards
    assert result["bg"] != "（背景待補充）", "bg should have real content for rich card"
    assert result["why"] != "（重要性待補充）", "why should have real content for rich card"


# ---------------------------------------------------------------------------
# T3 — Proof token extracted correctly
# ---------------------------------------------------------------------------

def test_t3_proof_token_extracted() -> None:
    """T3: Rich card with arXiv ID / version / $ amount → proof_line non-empty."""
    reset_stats()
    card = _rich_card()
    result = render_bbc_longform(card)

    assert result["proof_missing"] is False, (
        f"Expected proof_missing=False; proof_line='{result['proof_line']}'"
    )
    assert result["proof_line"], "Expected non-empty proof_line for card with known tokens"


# ---------------------------------------------------------------------------
# T4 — Double-call uses cache; stats NOT double-counted
# ---------------------------------------------------------------------------

def test_t4_double_call_uses_cache_no_double_stats() -> None:
    """T4: Calling render_bbc_longform twice on same card must not double stats."""
    reset_stats()
    card = _rich_card()

    result1 = render_bbc_longform(card)
    result2 = render_bbc_longform(card)

    # Results must be identical (same object from cache)
    assert result1 is result2, "Second call must return cached result (same object)"

    # Stats must show only 1 card processed despite 2 calls
    with __import__("utils.longform_narrative", fromlist=["_stats_lock"])._stats_lock:
        total = _stats["total_cards_processed"]
    assert total == 1, f"Expected 1 card in stats after 2 calls; got {total}"


# ---------------------------------------------------------------------------
# T5 — proof_line always contains ISO date YYYY-MM-DD and source_name
# ---------------------------------------------------------------------------

def test_t5_proof_line_contains_iso_date_and_source() -> None:
    """T5: proof_line must contain YYYY-MM-DD ISO date; proof_missing must be False."""
    import re as _re
    iso_pat = _re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

    reset_stats()
    # Case A: rich card (has arXiv natural token + date fallback)
    card_rich = _rich_card()
    result = render_bbc_longform(card_rich)
    assert iso_pat.search(result["proof_line"]), (
        f"No ISO date in proof_line (rich card): '{result['proof_line']}'"
    )
    assert result["proof_missing"] is False, (
        f"proof_missing must be False for rich card; proof_line='{result['proof_line']}'"
    )

    # Case B: short/ineligible card — proof_line should still have ISO date
    reset_stats()
    card_short = _short_card()
    result2 = render_bbc_longform(card_short)
    assert iso_pat.search(result2["proof_line"]), (
        f"No ISO date in proof_line (short card): '{result2['proof_line']}'"
    )
    assert result2["proof_missing"] is False, (
        "proof_missing must be False even for ineligible card (date fallback must fire)"
    )

    # Case C: card with explicit published_at declared field — date must appear
    reset_stats()
    card_dated = _StubCard(
        title_plain="Test dated card",
        what_happened="Something happened on 2025-11-30.",
        why_important="It matters for " * 100,   # pad to hit MIN_ANCHOR_CHARS
        source_name="TestSource",
        published_at="2025-11-30T12:00:00Z",
    )
    result3 = render_bbc_longform(card_dated)
    assert "2025-11-30" in result3["proof_line"], (
        f"Expected '2025-11-30' in proof_line; got '{result3['proof_line']}'"
    )
    assert "TestSource" in result3["proof_line"], (
        f"Expected 'TestSource' in proof_line; got '{result3['proof_line']}'"
    )


# ---------------------------------------------------------------------------
# T6 — write_longform_meta(event_cards=...) counts are self-consistent
# ---------------------------------------------------------------------------

def test_t6_meta_counts_self_consistent() -> None:
    """T6: write_longform_meta(event_cards) must satisfy:
       - total_cards_processed == len(event_cards)
       - eligible + ineligible == total
       - proof_present + proof_missing_count == eligible  (only eligible counted)
       - proof_coverage_ratio == proof_present / eligible  (>= 0.8 with fallback)
    """
    import json
    import tempfile
    from pathlib import Path
    from utils.longform_narrative import write_longform_meta, _CACHE_ATTR

    reset_stats()

    # Mix: 2 rich (eligible) + 1 short (ineligible)
    cards = [_rich_card(), _rich_card(), _short_card()]
    for c in cards:
        render_bbc_longform(c)

    with tempfile.TemporaryDirectory() as tmpdir:
        write_longform_meta(event_cards=cards, outdir=tmpdir)
        meta_path = Path(tmpdir) / "exec_longform.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    total    = meta["total_cards_processed"]
    eligible = meta["eligible_count"]
    inelig   = meta["ineligible_count"]
    proof_p  = meta["proof_present_count"]
    proof_m  = meta["proof_missing_count"]
    pcr      = meta["proof_coverage_ratio"]

    assert total == len(cards), (
        f"total_cards_processed={total} != len(cards)={len(cards)}"
    )
    assert eligible + inelig == total, (
        f"eligible({eligible}) + ineligible({inelig}) != total({total})"
    )
    assert proof_p + proof_m == eligible, (
        f"proof_present({proof_p}) + proof_missing({proof_m}) != eligible({eligible})"
    )
    # With date-fallback: all eligible cards have proof_missing=False → pcr == 1.0
    assert pcr >= 0.8, (
        f"proof_coverage_ratio={pcr} < 0.8 (date-fallback proof_line must drive pcr up)"
    )
