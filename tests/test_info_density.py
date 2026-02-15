from __future__ import annotations

from dataclasses import dataclass

from core.info_density import (
    apply_density_gate,
    apply_density_tiering,
    classify_density_tier,
    evaluate_text_density,
    info_density_breakdown,
)


@dataclass
class _Item:
    body: str


def test_info_density_breakdown_high_signal_text() -> None:
    text = (
        "OpenAI launched GPT-5.3 on 2026-02-15 with a 20% cost reduction. "
        "Microsoft Azure integrated the model across 12 regions in under 90 ms latency. "
        "Enterprise teams reported 3x workflow speedup in support automation."
    )
    b = info_density_breakdown(text)
    assert b.entity_hits >= 3
    assert b.numeric_hits >= 3
    assert b.sentence_count >= 3
    assert b.boilerplate_hits == 0
    assert b.fragment_penalty == 0
    assert b.score >= 80


def test_info_density_fragment_rejected() -> None:
    ok, reason, breakdown = evaluate_text_density("Last July was...", "event")
    assert ok is False
    assert reason == "fragment_placeholder"
    assert breakdown.fragment_penalty >= 1


def test_info_density_boilerplate_penalty() -> None:
    text = "Monitoring continues. Stay tuned for highlights and overview."
    ok, reason, breakdown = evaluate_text_density(text, "signal")
    assert ok is False
    assert reason in {"boilerplate", "low_density_score", "fragment_placeholder"}
    assert breakdown.boilerplate_hits >= 1


def test_info_density_thresholds_for_event_signal_corp() -> None:
    event_text = (
        "NVIDIA released CUDA 13.2 on 2026-02-15 with 18% throughput gain. "
        "OpenAI benchmark showed 120 ms latency in production. "
        "Google reported cost savings of $1.2M across two quarters."
    )
    signal_text = (
        "Users reported tool adoption increase across teams. "
        "Platform usage trend remained stable this week."
    )
    corp_text = (
        "Microsoft expanded Copilot integration into Dynamics and Teams. "
        "The rollout adds enterprise controls and audit visibility."
    )

    assert evaluate_text_density(event_text, "event")[0] is True
    assert evaluate_text_density(signal_text, "signal")[0] is True
    assert evaluate_text_density(corp_text, "corp")[0] is True


def test_apply_density_gate_stats_and_reasons() -> None:
    items = [
        _Item(
            body=(
                "OpenAI released GPT-5.3 on 2026-02-15 with 20% price reduction. "
                "Microsoft Azure integration reached 12 regions."
            )
        ),
        _Item(body="Last July was..."),
        _Item(body="Monitoring continues. Stay tuned for highlights."),
    ]

    passed, rejected, stats, _ = apply_density_gate(items, "signal", text_getter=lambda x: x.body)
    assert len(passed) >= 1
    assert len(rejected) >= 1
    assert stats.total_in == 3
    assert stats.rejected_total == len(rejected)
    assert isinstance(stats.avg_score, float)
    assert stats.rejected_reason_top


def test_density_tiering_a_b_c_routes() -> None:
    tier_a = classify_density_tier(
        (
            "NVIDIA released CUDA 13.2 in 2026 with 18% throughput gain. "
            "OpenAI benchmark reported 120 ms latency across 12 regions. "
            "Microsoft published deployment cost reduction of 22%."
        ),
        "event",
    )
    assert tier_a.tier == "A"

    tier_b = classify_density_tier(
        (
            "OpenAI copilots reached 12 support teams this week. "
            "Microsoft integration reduced triage time by 30%."
        ),
        "event",
    )
    assert tier_b.tier == "B"
    assert "insufficient_sentences" in tier_b.reason_flags

    tier_c = classify_density_tier("Last July was...", "event")
    assert tier_c.tier == "C"
    assert "fragment_placeholder" in tier_c.reason_flags


def test_apply_density_tiering_sets_density_tier_attribute() -> None:
    items = [
        _Item(
            body=(
                "OpenAI launched GPT-5.3 in 2026 with 20% cost reduction. "
                "Microsoft Azure deployed it in 12 regions. "
                "Google benchmark showed 95 ms latency."
            )
        ),
        _Item(
            body=(
                "OpenAI copilots reached 12 support teams this week. "
                "Microsoft integration reduced triage time by 30%."
            )
        ),
        _Item(body="Last July was..."),
    ]

    tier_a, tier_b, tier_c, _ = apply_density_tiering(items, "event", text_getter=lambda x: x.body)
    assert len(tier_a) == 1
    assert len(tier_b) == 1
    assert len(tier_c) == 1
    assert getattr(items[0], "density_tier", "") == "A"
    assert getattr(items[1], "density_tier", "") == "B"
    assert getattr(items[2], "density_tier", "") == "C"
