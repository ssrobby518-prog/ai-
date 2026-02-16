from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

from core.content_gate import apply_adaptive_content_gate
from core.ingestion import filter_items
from schemas.models import RawItem


def _relaxed_candidate(seed: str) -> str:
    # ~700 chars with 2 sentences: fails strict(1200/3), passes relaxed(600/2).
    return (
        (f"{seed} AI inference rollout completed with measurable production impact across regions. " * 5)
        + (f"{seed} GPU teams validated latency, cost, and rollback controls under load. " * 5)
    )


def test_adaptive_gate_uses_relaxed_when_strict_too_low() -> None:
    items = [
        SimpleNamespace(body=_relaxed_candidate(f"Item {i}"))
        for i in range(6)
    ]

    kept, rejected_map, stats = apply_adaptive_content_gate(items, min_keep_items=6)

    assert len(kept) == 6
    assert stats.total == 6
    assert stats.passed_strict == 0
    assert stats.passed_relaxed == 6
    assert stats.rejected_total == 0
    assert rejected_map == {}
    assert isinstance(stats.rejected_reason_top, list)


def test_fragment_placeholder_rejected_even_in_relaxed_mode() -> None:
    items = [
        SimpleNamespace(title="AI update", body="Last July was..."),
        SimpleNamespace(title="AI update", body="This migration was..."),
        SimpleNamespace(title="AI inference update", body="OpenAI valid article body. " * 120),
    ]

    kept, rejected_map, stats = apply_adaptive_content_gate(items, min_keep_items=3)

    assert len(kept) == 1
    assert stats.rejected_by_reason.get("fragment_placeholder", 0) == 2
    assert any(reason == "fragment_placeholder" for reason in rejected_map.values())


def test_filter_items_logs_content_gate_summary(caplog) -> None:
    item = RawItem(
        item_id="gate-log-001",
        title="AI inference gate logging check",
        url="https://example.com/gate-log-001",
        body=("The AI model team completed GPU inference rollout validation with clear rollback controls. " * 30),
        published_at=datetime.now(UTC).isoformat(),
        source_name="test",
        source_category="tech",
        lang="en",
    )

    with caplog.at_level(logging.INFO):
        filter_items([item])

    assert any("ContentGate fetched_total=" in rec.message for rec in caplog.records)


def test_gate_not_starve_pipeline_soft_pass(monkeypatch) -> None:
    body = (
        "Short but coherent AI model analysis summary with explicit implications for GPU inference rollout. "
        "The update includes customer impact, operational constraints, and mitigation options. "
    ) * 5  # <1200 chars: strict fail, relaxed/soft pass.

    item = RawItem(
        item_id="gate-soft-pass-001",
        title="Short AI inference brief",
        url="https://example.com/gate-soft-pass-001",
        body=body,
        published_at=datetime.now(UTC).isoformat(),
        source_name="test",
        source_category="tech",
        lang="en",
    )

    monkeypatch.setattr("config.settings.EVENT_GATE_MIN_LEN", 1200)
    monkeypatch.setattr("config.settings.EVENT_GATE_MIN_SENTENCES", 3)
    monkeypatch.setattr("config.settings.SIGNAL_GATE_MIN_LEN", 300)
    monkeypatch.setattr("config.settings.SIGNAL_GATE_MIN_SENTENCES", 2)

    kept, summary = filter_items([item])
    assert len(kept) == 0
    assert summary.kept_count == 0
    assert len(summary.signal_pool) >= 1
    assert int(summary.gate_stats.get("event_gate_pass_total", 0)) == 0
    assert int(summary.gate_stats.get("signal_gate_pass_total", 0)) >= 1
