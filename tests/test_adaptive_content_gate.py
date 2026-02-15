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
        (f"{seed} rollout completed with measurable production impact across regions. " * 5)
        + (f"{seed} teams validated latency, cost, and rollback controls under load. " * 5)
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
        SimpleNamespace(body="Last July was..."),
        SimpleNamespace(body="This migration was..."),
        SimpleNamespace(body="Valid article body. " * 120),
    ]

    kept, rejected_map, stats = apply_adaptive_content_gate(items, min_keep_items=3)

    assert len(kept) == 1
    assert stats.rejected_by_reason.get("fragment_placeholder", 0) == 2
    assert any(reason == "fragment_placeholder" for reason in rejected_map.values())


def test_filter_items_logs_content_gate_summary(caplog) -> None:
    item = RawItem(
        item_id="gate-log-001",
        title="Adaptive gate logging check",
        url="https://example.com/gate-log-001",
        body=("The team completed rollout validation with clear rollback controls. " * 30),
        published_at=datetime.now(UTC).isoformat(),
        source_name="test",
        source_category="tech",
        lang="en",
    )

    with caplog.at_level(logging.INFO):
        filter_items([item])

    assert any("ContentGate strict_pass=" in rec.message for rec in caplog.records)
