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
    channel_min: int = 55,
    max_extra: int = 50,
) -> dict:
    """Replicate the run_once.py Z0 injection gate logic and return audit counts."""
    candidates_total = len(items)

    # Frontier filter (sorted descending, no top-N yet)
    frontier_passed = sorted(
        [it for it in items if int(getattr(it, "z0_frontier_score", 0) or 0) >= frontier_min],
        key=lambda it: int(getattr(it, "z0_frontier_score", 0) or 0),
        reverse=True,
    )
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

    selected = channel_passed[:max_extra]
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
