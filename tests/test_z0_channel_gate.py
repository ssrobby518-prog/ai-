"""Tests for Z0 channel gate injection (Option 1 — Plan Z0 channel gate injection).

Covers:
  - accepts_business_product_tech: pricing/launch, benchmark/weights,
    funding/acquisition signals pass the channel gate.
  - rejects_dev_commentary: vague personal-AI-usage / dev-commentary text
    is blocked by the channel gate.
  - meta_fields_present: z0_inject_* audit fields are self-consistent when
    the gate logic is simulated end-to-end.
"""

from __future__ import annotations

import pytest

from utils.topic_router import classify_channels

# Mirror of Z0_EXEC_MIN_CHANNEL default (config/settings.py)
_MIN_CHANNEL = 55


def _channel_passes(text: str, url: str = "") -> bool:
    """Replicate the run_once.py channel gate predicate."""
    ch = classify_channels(text, url)
    return max(ch["product_score"], ch["tech_score"], ch["business_score"]) >= _MIN_CHANNEL


# ---------------------------------------------------------------------------
# accepts_business_product_tech
# ---------------------------------------------------------------------------


class TestAcceptsBusinessProductTech:
    """Items with pricing/launch, benchmark/weights, or funding/acquisition signals must pass."""

    def test_product_launch_pricing(self):
        text = (
            "OpenAI launches GPT-4o at $0.01 per token pricing, "
            "generally available with a free tier subscription plan"
        )
        assert _channel_passes(text), "Expected channel pass for product launch/pricing text"

    def test_tech_benchmark_weights(self):
        text = (
            "Llama 3 70B model weights released — benchmark results: MMLU 85.2%, "
            "HumanEval 78.4% checkpoint available for fine-tuning inference"
        )
        assert _channel_passes(text), "Expected channel pass for benchmark/weights text"

    def test_business_funding_acquisition(self):
        text = (
            "Anthropic raises $750M in Series D funding round, "
            "acquires AI safety startup to expand enterprise revenue"
        )
        assert _channel_passes(text), "Expected channel pass for funding/acquisition text"

    def test_product_api_update(self):
        text = (
            "Anthropic Claude API v2.1 update: new feature released, pricing tiers updated, "
            "generally available for enterprise customers with a new subscription plan"
        )
        assert _channel_passes(text), "Expected channel pass for API update/pricing text"

    def test_business_partnership_revenue(self):
        text = (
            "Microsoft and OpenAI expand partnership deal worth $10B, "
            "ARR growing 120% with IPO valuation exceeding $100B"
        )
        assert _channel_passes(text), "Expected channel pass for partnership/revenue text"


# ---------------------------------------------------------------------------
# rejects_dev_commentary
# ---------------------------------------------------------------------------


class TestRejectsDevCommentary:
    """Dev commentary / vague AI opinion pieces must be blocked by the channel gate."""

    def test_rejects_personal_ai_usage(self):
        text = "I use AI in my daily workflow to help me organize tasks and write emails."
        assert not _channel_passes(text), (
            "Expected channel gate to block personal AI usage commentary"
        )

    def test_rejects_when_ai_content_isnt(self):
        text = (
            "When AI content isn't properly curated by editors, "
            "readers may be disappointed with the overall quality of outputs."
        )
        assert not _channel_passes(text), (
            "Expected channel gate to block vague AI editorial commentary"
        )

    def test_rejects_generic_ai_opinion(self):
        text = "AI is changing the way we think about work and creativity in the modern era."
        assert not _channel_passes(text), (
            "Expected channel gate to block generic AI opinion piece"
        )

    def test_rejects_dev_discussion_no_signals(self):
        text = (
            "Discussion: how should developers approach AI integration "
            "in their existing systems?"
        )
        assert not _channel_passes(text), (
            "Expected channel gate to block dev discussion without product/business signals"
        )


# ---------------------------------------------------------------------------
# meta_fields_present
# ---------------------------------------------------------------------------


class _MockItem:
    """Minimal stand-in for a RawItem used in gate simulation tests."""

    def __init__(self, title: str, body: str, url: str = "", frontier_score: int = 80) -> None:
        self.title = title
        self.body = body
        self.url = url
        self.z0_frontier_score = frontier_score


def _simulate_injection_gate(
    items: list,
    frontier_min: int = 65,
    frontier_min_biz: int = 45,
    channel_min: int = 55,
    max_extra: int = 50,
) -> dict:
    """Replicate the run_once.py Z0 injection gate logic (three-track) and return audit counts.

    Track A (standard): frontier >= frontier_min — any channel
    Track B (business supplement): frontier >= frontier_min_biz AND best_channel=="business"
                                    AND business_score >= channel_min
    Track C (product supplement):  frontier >= frontier_min_biz AND best_channel=="product"
                                    AND product_score  >= channel_min
    """
    candidates_total = len(items)
    _BIZ_RESERVE = 4
    _PROD_RESERVE = 4

    # Track A: standard frontier (any channel)
    track_a_ids: set[str] = set()
    track_a: list = []
    for it in items:
        fs = int(getattr(it, "z0_frontier_score", 0) or 0)
        if fs >= frontier_min:
            iid = str(getattr(it, "item_id", "") or id(it))
            track_a_ids.add(iid)
            track_a.append(it)

    # Tracks B (business) and C (product): supplement from full pool
    track_b: list = []
    track_c: list = []
    for it in items:
        fs = int(getattr(it, "z0_frontier_score", 0) or 0)
        iid = str(getattr(it, "item_id", "") or id(it))
        if iid in track_a_ids or fs < frontier_min_biz:
            continue
        text = f"{getattr(it, 'title', '') or ''} {getattr(it, 'body', '') or ''}"
        url = str(getattr(it, "url", "") or "")
        ch = classify_channels(text, url)
        if ch["best_channel"] == "business" and ch["business_score"] >= channel_min:
            track_b.append(it)
        elif ch["best_channel"] == "product" and ch["product_score"] >= channel_min:
            track_c.append(it)

    track_a.sort(key=lambda it: int(getattr(it, "z0_frontier_score", 0) or 0), reverse=True)
    track_b.sort(key=lambda it: int(getattr(it, "z0_frontier_score", 0) or 0), reverse=True)
    track_c.sort(key=lambda it: int(getattr(it, "z0_frontier_score", 0) or 0), reverse=True)
    frontier_passed = track_a + track_b + track_c
    after_frontier_total = len(frontier_passed)

    # Channel gate: max(product, tech, business) >= threshold
    def _passes(it) -> bool:
        text = f"{getattr(it, 'title', '') or ''} {getattr(it, 'body', '') or ''}"
        url = str(getattr(it, "url", "") or "")
        ch = classify_channels(text, url)
        return max(ch["product_score"], ch["tech_score"], ch["business_score"]) >= channel_min

    channel_passed = [it for it in frontier_passed if _passes(it)]
    after_channel_gate_total = len(channel_passed)
    dropped = after_frontier_total - after_channel_gate_total

    # Additive supplement selection: Track A fills full budget, B and C appended
    track_b_ids = {str(getattr(it, "item_id", "") or id(it)) for it in track_b}
    track_c_ids = {str(getattr(it, "item_id", "") or id(it)) for it in track_c}
    ch_pass_b = [it for it in channel_passed if str(getattr(it, "item_id", "") or id(it)) in track_b_ids]
    ch_pass_c = [it for it in channel_passed if str(getattr(it, "item_id", "") or id(it)) in track_c_ids]
    ch_pass_a = [it for it in channel_passed
                 if str(getattr(it, "item_id", "") or id(it)) not in (track_b_ids | track_c_ids)]
    selected = ch_pass_a[:max_extra] + ch_pass_b[:_BIZ_RESERVE] + ch_pass_c[:_PROD_RESERVE]
    selected_total = len(selected)

    return {
        "z0_inject_candidates_total": candidates_total,
        "z0_inject_after_frontier_total": after_frontier_total,
        "z0_inject_after_channel_gate_total": after_channel_gate_total,
        "z0_inject_selected_total": selected_total,
        "z0_inject_dropped_by_channel_gate": dropped,
        "z0_inject_channel_gate_threshold": channel_min,
    }


class TestMetaFieldsPresent:
    """Simulate the Z0 injection pipeline and verify field existence + self-consistency."""

    def test_all_required_fields_present(self):
        items = [
            _MockItem(
                "OpenAI GPT-5 launch pricing $20/month subscription",
                "OpenAI launches GPT-5 with new pricing tiers and subscription plan",
                frontier_score=80,
            )
        ]
        meta = _simulate_injection_gate(items)
        required = [
            "z0_inject_candidates_total",
            "z0_inject_after_frontier_total",
            "z0_inject_after_channel_gate_total",
            "z0_inject_selected_total",
            "z0_inject_dropped_by_channel_gate",
            "z0_inject_channel_gate_threshold",
        ]
        for field in required:
            assert field in meta, f"Missing required audit field: {field}"

    def test_selected_le_after_gate_le_after_frontier_le_candidates(self):
        items = [
            _MockItem(
                "GPT-5 launch pricing $20/mo plan",
                "New product feature released with subscription",
                frontier_score=80,
            ),
            _MockItem(
                "Benchmark MMLU 90% weights checkpoint inference",
                "Tech evaluation results 90% on benchmark",
                frontier_score=75,
            ),
            _MockItem(
                "I use AI daily workflow tasks",
                "Personal AI usage for productivity",
                frontier_score=70,  # passes frontier, likely fails channel
            ),
            _MockItem(
                "Generic AI thoughts creativity",
                "AI is changing society and work",
                frontier_score=50,  # below frontier threshold
            ),
        ]
        meta = _simulate_injection_gate(items)
        assert meta["z0_inject_selected_total"] <= meta["z0_inject_after_channel_gate_total"], (
            f"selected={meta['z0_inject_selected_total']} > "
            f"after_gate={meta['z0_inject_after_channel_gate_total']}"
        )
        assert meta["z0_inject_after_channel_gate_total"] <= meta["z0_inject_after_frontier_total"], (
            f"after_gate={meta['z0_inject_after_channel_gate_total']} > "
            f"after_frontier={meta['z0_inject_after_frontier_total']}"
        )
        assert meta["z0_inject_after_frontier_total"] <= meta["z0_inject_candidates_total"], (
            f"after_frontier={meta['z0_inject_after_frontier_total']} > "
            f"candidates={meta['z0_inject_candidates_total']}"
        )

    def test_dropped_equals_frontier_minus_channel_gate(self):
        items = [
            _MockItem(
                "GPT-5 launch pricing $20/mo subscription plan",
                "OpenAI product feature released",
                frontier_score=80,
            ),
            _MockItem(
                "I use AI in my daily life",
                "Personal AI usage commentary no channel signal",
                frontier_score=75,  # passes frontier, expect to fail channel
            ),
        ]
        meta = _simulate_injection_gate(items)
        expected_dropped = (
            meta["z0_inject_after_frontier_total"] - meta["z0_inject_after_channel_gate_total"]
        )
        assert meta["z0_inject_dropped_by_channel_gate"] == expected_dropped, (
            f"dropped_by_channel_gate={meta['z0_inject_dropped_by_channel_gate']} "
            f"!= frontier_total - channel_gate_total={expected_dropped}"
        )

    def test_all_below_frontier_gives_zero_selected(self):
        items = [
            _MockItem("GPT-5 pricing launch", "product launch subscription", frontier_score=30),
            _MockItem("MMLU benchmark weights", "tech benchmark results", frontier_score=40),
        ]
        meta = _simulate_injection_gate(items, frontier_min=65)
        assert meta["z0_inject_after_frontier_total"] == 0
        assert meta["z0_inject_selected_total"] == 0

    def test_channel_gate_threshold_recorded_correctly(self):
        meta = _simulate_injection_gate([], channel_min=55)
        assert meta["z0_inject_channel_gate_threshold"] == 55

    def test_empty_pool_all_zeros(self):
        meta = _simulate_injection_gate([])
        assert meta["z0_inject_candidates_total"] == 0
        assert meta["z0_inject_after_frontier_total"] == 0
        assert meta["z0_inject_after_channel_gate_total"] == 0
        assert meta["z0_inject_selected_total"] == 0
        assert meta["z0_inject_dropped_by_channel_gate"] == 0


# ---------------------------------------------------------------------------
# TestBusinessRelaxedTrack — Track B (frontier >= 45, best_channel == "business")
# ---------------------------------------------------------------------------


class TestBusinessRelaxedTrack:
    """Track B: business articles with frontier 45-64 pass via the relaxed gate."""

    def _biz_item(self, frontier: int) -> _MockItem:
        """A pure business-best_channel item at the given frontier score."""
        return _MockItem(
            title="AI startup raises $200M Series C funding round from investors",
            body=(
                "The company closed a $200M Series C led by top investors, "
                "pushing the valuation to $2B.  ARR has grown 3× year-over-year. "
                "CEO announced expansion into enterprise customer markets."
            ),
            url="https://news.google.com/articles/ai-startup-series-c",
            frontier_score=frontier,
        )

    def test_business_item_at_frontier_60_passes_track_b(self):
        """frontier=60 < 65 (Track A) but >= 45 (Track B) — should be admitted."""
        item = self._biz_item(frontier=60)
        meta = _simulate_injection_gate([item], frontier_min=65, frontier_min_biz=45, channel_min=55)
        assert meta["z0_inject_selected_total"] >= 1, (
            "Business item with frontier=60 should pass via Track B"
        )

    def test_business_item_at_frontier_45_passes_track_b(self):
        """frontier exactly at Track B threshold should be admitted."""
        item = self._biz_item(frontier=45)
        meta = _simulate_injection_gate([item], frontier_min=65, frontier_min_biz=45, channel_min=55)
        assert meta["z0_inject_selected_total"] >= 1, (
            "Business item with frontier=45 should pass via Track B"
        )

    def test_business_item_below_biz_threshold_rejected(self):
        """frontier=44 < 45 (Track B) — should be rejected even if best_channel=business."""
        item = self._biz_item(frontier=44)
        meta = _simulate_injection_gate([item], frontier_min=65, frontier_min_biz=45, channel_min=55)
        assert meta["z0_inject_selected_total"] == 0, (
            "Business item with frontier=44 < frontier_min_biz=45 must be rejected"
        )

    def test_non_business_item_at_frontier_60_rejected_by_track_a(self):
        """frontier=60 with best_channel=tech (not business) must NOT pass Track B."""
        tech_item = _MockItem(
            title="New LLM benchmark: MMLU 90% on 70B model weights checkpoint",
            body=(
                "Researchers release model weights and arXiv paper. "
                "Benchmark suite shows 90% on MMLU with 70B parameter architecture. "
                "Fine-tuning recipe and inference latency results included."
            ),
            url="https://arxiv.org/abs/2402.12345",
            frontier_score=60,
        )
        meta = _simulate_injection_gate([tech_item], frontier_min=65, frontier_min_biz=45, channel_min=55)
        # tech_item has frontier=60 (Track A needs 65, so fails Track A).
        # classify_channels should return best_channel=tech, NOT business → fails Track B.
        assert meta["z0_inject_selected_total"] == 0, (
            "Tech item with frontier=60 should NOT pass Track B (Track B is business-only)"
        )

    def test_business_track_b_item_counted_in_after_frontier_total(self):
        """Items admitted via Track B must be counted in z0_inject_after_frontier_total."""
        item = self._biz_item(frontier=55)
        meta = _simulate_injection_gate([item], frontier_min=65, frontier_min_biz=45, channel_min=55)
        assert meta["z0_inject_after_frontier_total"] >= 1, (
            "Track B item should be included in z0_inject_after_frontier_total"
        )


# ---------------------------------------------------------------------------
# TestProductRelaxedTrack — Track C (frontier >= 45, best_channel == "product")
# ---------------------------------------------------------------------------


class TestProductRelaxedTrack:
    """Track C: product-announcement articles with frontier 45-64 pass via the product supplement."""

    def _prod_item(self, frontier: int) -> _MockItem:
        """A pure product-best_channel item at the given frontier score."""
        return _MockItem(
            title="OpenAI launches GPT-4o at $0.01/token pricing, generally available",
            body=(
                "OpenAI has released GPT-4o with updated pricing tiers: $0.01 per token "
                "input, $0.03 per token output.  The new model is generally available via "
                "the API and ChatGPT subscription plan."
            ),
            url="https://openai.com/blog/gpt-4o-pricing",
            frontier_score=frontier,
        )

    def test_product_item_at_frontier_60_passes_track_c(self):
        """frontier=60 < 65 (Track A) but >= 45 (Track C) — should be admitted."""
        item = self._prod_item(frontier=60)
        meta = _simulate_injection_gate([item], frontier_min=65, frontier_min_biz=45, channel_min=55)
        assert meta["z0_inject_selected_total"] >= 1, (
            "Product item with frontier=60 should pass via Track C"
        )

    def test_product_item_at_frontier_45_passes_track_c(self):
        """frontier exactly at Track C threshold should be admitted."""
        item = self._prod_item(frontier=45)
        meta = _simulate_injection_gate([item], frontier_min=65, frontier_min_biz=45, channel_min=55)
        assert meta["z0_inject_selected_total"] >= 1, (
            "Product item with frontier=45 should pass via Track C"
        )

    def test_product_item_below_threshold_rejected(self):
        """frontier=44 < 45 — rejected even if best_channel=product."""
        item = self._prod_item(frontier=44)
        meta = _simulate_injection_gate([item], frontier_min=65, frontier_min_biz=45, channel_min=55)
        assert meta["z0_inject_selected_total"] == 0, (
            "Product item with frontier=44 < frontier_min_biz=45 must be rejected"
        )

    def test_non_product_item_at_frontier_60_not_admitted_via_track_c(self):
        """frontier=60 with best_channel=business must NOT pass Track C."""
        biz_item = _MockItem(
            title="AI startup raises $200M Series C funding from investors valuation $2B",
            body="The company closed a $200M funding round, valuation $2B, CEO announced expansion.",
            url="https://news.google.com/biz",
            frontier_score=60,
        )
        meta = _simulate_injection_gate([biz_item], frontier_min=65, frontier_min_biz=45, channel_min=55)
        # best_channel=business, so Track B admits it but NOT Track C
        assert meta["z0_inject_selected_total"] >= 1, (
            "Business item should still pass via Track B (not Track C)"
        )

    def test_track_b_and_c_items_both_counted(self):
        """Track B and Track C items both count in z0_inject_after_frontier_total."""
        biz_item = _MockItem(
            title="AI startup raises $200M Series C funding from investors valuation $2B",
            body="The company closed a $200M funding round, valuation $2B, CEO announced expansion.",
            url="https://news.google.com/biz",
            frontier_score=60,
        )
        prod_item = self._prod_item(frontier=55)
        meta = _simulate_injection_gate(
            [biz_item, prod_item], frontier_min=65, frontier_min_biz=45, channel_min=55
        )
        assert meta["z0_inject_after_frontier_total"] >= 2, (
            "Both Track B and Track C items should appear in z0_inject_after_frontier_total"
        )
        assert meta["z0_inject_selected_total"] >= 2, (
            "Both Track B and Track C items should be selected"
        )
