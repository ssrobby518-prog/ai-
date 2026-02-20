"""Offline tests for the four executive quality gates (G1-G4).

All tests are pure-Python — no network, no pipeline, no PowerShell.

G1) AI_RELEVANCE_GATE  — non-AI content is rejected by is_relevant_ai()
G2) SOURCE_DIVERSITY_GATE — single-source dominance flagged as FAIL
G3) PROOF_COVERAGE_GATE   — events lacking hard evidence tokens flagged as FAIL
G4) FRAGMENT_LEAK_GATE    — placeholder/fragment text detected, leaked=0 enforcement
"""
from __future__ import annotations

import types
import pytest

from utils.topic_router import is_relevant_ai
from utils.semantic_quality import is_placeholder_or_fragment
from utils.narrative_compact import count_hard_evidence_tokens
from core.content_strategy import _compute_source_diversity, _compute_proof_coverage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_card(**kwargs) -> types.SimpleNamespace:
    """Create a minimal mock EduNewsCard-like object for gate tests."""
    defaults = {
        "source_name": "hackernews",
        "source_url": "https://news.ycombinator.com/item?id=1",
        "title_plain": "Test",
        "what_happened": "",
        "why_important": "",
        "technical_interpretation": "",
        "id": "test-id-1",
    }
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# G1) AI RELEVANCE GATE
# ---------------------------------------------------------------------------

class TestAIRelevanceGate:
    """G1: Non-AI content must be rejected by is_relevant_ai()."""

    def test_car_lawsuit_rejected(self):
        """Non-AI car lawsuit / court settlement → no AI core → FAIL.

        Note: major AI-company names (Tesla, OpenAI, etc.) are whitelisted
        in topic_router.py and will PASS.  We use a non-whitelisted company.
        """
        text = "Ford ordered to pay $200M in car crash lawsuit settlement by California court"
        relevant, reasons = is_relevant_ai(text)
        assert relevant is False, f"Expected FAIL but got PASS. Reasons: {reasons}"

    def test_real_estate_rejected(self):
        """Real-estate news without any AI content → FAIL (hard negative)."""
        text = "Manhattan apartment prices surge as housing market recovers. Building permits up 12%."
        relevant, reasons = is_relevant_ai(text)
        assert relevant is False, f"Expected FAIL but got PASS. Reasons: {reasons}"

    def test_local_news_no_ai_rejected(self):
        """Generic local business news with no AI mentions → FAIL."""
        text = "Local restaurant chain expands to 50 locations across the midwest with new franchise deals"
        relevant, reasons = is_relevant_ai(text)
        assert relevant is False, f"Expected FAIL but got PASS. Reasons: {reasons}"

    def test_openai_funding_passes(self):
        """OpenAI funding news → whitelist hit → PASS."""
        relevant, reasons = is_relevant_ai("OpenAI raises $6.6B in new funding round")
        assert relevant is True

    def test_nvidia_gpu_ai_passes(self):
        """NVIDIA GPU for AI → whitelist + AI core → PASS."""
        relevant, reasons = is_relevant_ai("NVIDIA H100 GPU powers new LLM inference cluster")
        assert relevant is True

    def test_llm_benchmark_passes(self):
        """LLM benchmark result → AI core → PASS."""
        relevant, reasons = is_relevant_ai("New LLM achieves 90.2% on MMLU benchmark, outperforming GPT-4")
        assert relevant is True


# ---------------------------------------------------------------------------
# G2) SOURCE DIVERSITY GATE
# ---------------------------------------------------------------------------

class TestSourceDiversityGate:
    """G2: Single-source dominance (>45%) must be flagged as FAIL."""

    def test_hackernews_dominates_fails(self):
        """10 events all from HackerNews → share=100% > 45% → FAIL."""
        cards = [
            _make_card(source_name="hackernews", id=f"hn-{i}")
            for i in range(10)
        ]
        result = _compute_source_diversity(cards, max_share=0.45)
        assert result["source_diversity_gate"] == "FAIL", (
            f"Expected FAIL but got PASS: share={result['max_source_share']}"
        )
        assert result["max_source"] == "hackernews"
        assert result["max_source_share"] == 1.0

    def test_diverse_sources_pass(self):
        """5 sources, 2 events each → max share = 20% → PASS."""
        sources = ["hackernews", "reddit", "arxiv", "techcrunch", "venturebeat"]
        cards = [
            _make_card(source_name=src, id=f"{src}-{i}")
            for i in range(2)
            for src in sources
        ]
        result = _compute_source_diversity(cards, max_share=0.45)
        assert result["source_diversity_gate"] == "PASS", (
            f"Expected PASS but got FAIL: share={result['max_source_share']}"
        )

    def test_exactly_at_threshold_passes(self):
        """4 events from HN out of 10 total = 40% ≤ 45% → PASS."""
        cards = (
            [_make_card(source_name="hackernews", id=f"hn-{i}") for i in range(4)] +
            [_make_card(source_name="arxiv",      id=f"ax-{i}") for i in range(3)] +
            [_make_card(source_name="reddit",     id=f"rd-{i}") for i in range(3)]
        )
        result = _compute_source_diversity(cards, max_share=0.45)
        assert result["source_diversity_gate"] == "PASS"
        assert result["max_source_share"] == pytest.approx(0.4, abs=0.01)

    def test_just_above_threshold_fails(self):
        """5 events from HN out of 10 = 50% > 45% → FAIL."""
        cards = (
            [_make_card(source_name="hackernews", id=f"hn-{i}") for i in range(5)] +
            [_make_card(source_name="other",      id=f"ot-{i}") for i in range(5)]
        )
        result = _compute_source_diversity(cards, max_share=0.45)
        assert result["source_diversity_gate"] == "FAIL"

    def test_events_by_source_map_populated(self):
        """events_by_source counts per source are correct."""
        cards = [
            _make_card(source_name="arxiv"),
            _make_card(source_name="arxiv"),
            _make_card(source_name="reddit"),
        ]
        result = _compute_source_diversity(cards)
        assert result["events_by_source"]["arxiv"] == 2
        assert result["events_by_source"]["reddit"] == 1


# ---------------------------------------------------------------------------
# G3) PROOF COVERAGE GATE
# ---------------------------------------------------------------------------

class TestProofCoverageGate:
    """G3: Events lacking hard evidence tokens bring coverage below threshold → FAIL."""

    def test_no_evidence_fails(self):
        """Event with no version / money / benchmark / date → no proof token → FAIL."""
        cards = [
            _make_card(
                title_plain="Company launches new product",
                what_happened="The company announced a new product today.",
                why_important="This is significant for the industry.",
                id="ev-1",
            )
        ]
        result = _compute_proof_coverage(cards, min_coverage=0.85)
        assert result["proof_coverage_gate"] == "FAIL", (
            f"Expected FAIL but got PASS: ratio={result['proof_coverage_ratio']}"
        )
        assert "ev-1" in result["proof_missing_event_ids"]

    def test_version_number_passes(self):
        """Event with 'v1.2.3' → proof token hit → PASS."""
        cards = [
            _make_card(
                what_happened="OpenAI released GPT-4o v1.2.3 with improved reasoning.",
                id="ev-v",
            )
        ]
        result = _compute_proof_coverage(cards, min_coverage=0.85)
        assert result["proof_coverage_gate"] == "PASS"
        assert result["proof_coverage_ratio"] == 1.0

    def test_dollar_amount_passes(self):
        """Event with '$100M' → proof token hit → PASS."""
        cards = [
            _make_card(
                what_happened="Anthropic raised $100M in Series C funding.",
                id="ev-m",
            )
        ]
        result = _compute_proof_coverage(cards, min_coverage=0.85)
        assert result["proof_coverage_gate"] == "PASS"

    def test_benchmark_score_passes(self):
        """Event with MMLU benchmark score → proof token hit → PASS."""
        cards = [
            _make_card(
                what_happened="Model achieves 89.5% on MMLU benchmark.",
                id="ev-b",
            )
        ]
        # count_hard_evidence_tokens should find "89.5%" or the number
        tokens = count_hard_evidence_tokens(cards[0].what_happened)
        assert tokens > 0, "Expected at least one proof token for benchmark+score text"

    def test_mixed_coverage_threshold(self):
        """8/10 events with proof → 80% < 85% threshold → FAIL."""
        cards_with = [
            _make_card(what_happened=f"OpenAI v{i}.0 release.", id=f"ev-{i}")
            for i in range(8)
        ]
        cards_without = [
            _make_card(what_happened="New capability announced.", id=f"ev-noproof-{i}")
            for i in range(2)
        ]
        result = _compute_proof_coverage(cards_with + cards_without, min_coverage=0.85)
        assert result["proof_coverage_gate"] == "FAIL"
        assert result["proof_coverage_ratio"] == pytest.approx(0.8, abs=0.01)

    def test_high_coverage_passes(self):
        """9/10 events with proof → 90% > 85% → PASS."""
        cards_with = [
            _make_card(what_happened=f"Claude 3.{i} raises $500M.", id=f"ev-{i}")
            for i in range(9)
        ]
        cards_without = [
            _make_card(what_happened="Update announced.", id="ev-noproof")
        ]
        result = _compute_proof_coverage(cards_with + cards_without, min_coverage=0.85)
        assert result["proof_coverage_gate"] == "PASS"

    def test_zero_events_passes(self):
        """Empty list → ratio = 1.0 (vacuously) → PASS."""
        result = _compute_proof_coverage([], min_coverage=0.85)
        assert result["proof_coverage_gate"] == "PASS"
        assert result["proof_coverage_ratio"] == 1.0


# ---------------------------------------------------------------------------
# G4) FRAGMENT ZERO-TOLERANCE GATE
# ---------------------------------------------------------------------------

class TestFragmentLeakGate:
    """G4: Fragment/placeholder text must be detected and fixed; leaked=0."""

    @pytest.mark.parametrize("fragment_text", [
        "的趨勢，解決方 記",
        "Last July was",
        "2.",
        "",
        "   ",
        "WHY IT MATTERS:",
    ])
    def test_known_fragment_detected(self, fragment_text: str):
        """Known fragment strings must be detected by is_placeholder_or_fragment."""
        assert is_placeholder_or_fragment(fragment_text), (
            f"Expected fragment detection for: {fragment_text!r}"
        )

    def test_substantial_text_not_fragment(self):
        """Substantive AI news text must NOT be flagged as fragment."""
        text = (
            "OpenAI released GPT-4o, a new multimodal model scoring 90.2% on MMLU. "
            "The model integrates vision and text understanding, making it suitable for "
            "enterprise AI workflows requiring both structured and unstructured data analysis."
        )
        assert not is_placeholder_or_fragment(text), (
            "Substantive text should not be flagged as fragment"
        )

    def test_fragment_leak_gate_pass_when_all_fixed(self):
        """If fragments_leaked == 0, gate is PASS."""
        # Simulate the exec_quality.meta logic directly
        fragments_detected = 3
        fragments_fixed = 3
        fragments_leaked = max(0, fragments_detected - fragments_fixed)
        gate = "PASS" if fragments_leaked == 0 else "FAIL"
        assert gate == "PASS"
        assert fragments_leaked == 0

    def test_fragment_leak_gate_fail_when_leaked(self):
        """If fragments_leaked > 0, gate is FAIL."""
        fragments_detected = 3
        fragments_fixed = 2
        fragments_leaked = max(0, fragments_detected - fragments_fixed)
        gate = "PASS" if fragments_leaked == 0 else "FAIL"
        assert gate == "FAIL"
        assert fragments_leaked == 1

    def test_count_hard_evidence_on_fragment_is_zero(self):
        """Pure fragment text has zero hard evidence tokens."""
        tokens = count_hard_evidence_tokens("的趨勢，解決方 記")
        assert tokens == 0

    def test_count_hard_evidence_on_real_content(self):
        """Real AI content with a version number has ≥ 1 hard evidence token."""
        tokens = count_hard_evidence_tokens(
            "Anthropic Claude 3.5 Sonnet achieves 72.0% on SWE-bench Verified."
        )
        assert tokens >= 1, f"Expected >= 1 proof tokens, got {tokens}"
