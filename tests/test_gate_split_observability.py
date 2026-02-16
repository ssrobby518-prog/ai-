from __future__ import annotations

import logging
from datetime import UTC, datetime

from core.ingestion import filter_items
from schemas.models import RawItem


def _mk_item(item_id: str, title: str, body: str, url: str) -> RawItem:
    return RawItem(
        item_id=item_id,
        title=title,
        url=url,
        body=body,
        published_at=datetime.now(UTC).isoformat(),
        source_name="test-source",
        source_category="tech",
        lang="en",
    )


def test_gate_split_observability_and_counts(caplog) -> None:
    event_body = (
        "OpenAI GPT-5.2 rollout reached 12 enterprise teams in 2026 with 35% latency reduction. "
        "Microsoft Azure benchmark confirmed 90ms response and $2.1M annual cost savings. "
        "Google and NVIDIA jointly reported production stability across 3 regions."
    ) * 6
    signal_body = (
        "Bilibili creator workflow changed after GPT integration in 2026. "
        "Teams observed 2x task completion speed with measurable adoption."
    ) * 3
    reject_body = (
        "Weekly roundup with top links for this week. Subscribe and sign in to continue reading the digest. "
        "This index page only lists links and login prompts without concrete event details."
    )

    items = [
        _mk_item("event-001", "OpenAI enterprise rollout", event_body, "https://example.com/event-001"),
        _mk_item("signal-001", "Creator workflow change", signal_body, "https://example.com/signal-001"),
        _mk_item("reject-001", "Digest page", reject_body, "https://example.com/reject-001"),
    ]

    with caplog.at_level(logging.INFO):
        kept, summary = filter_items(items)

    assert len(kept) >= 1
    assert int(summary.gate_stats.get("event_gate_pass_total", 0)) >= 1
    assert int(summary.gate_stats.get("signal_gate_pass_total", 0)) >= 2
    assert int(summary.gate_stats.get("signal_gate_pass_total", 0)) >= int(
        summary.gate_stats.get("event_gate_pass_total", 0)
    )
    assert summary.signal_pool
    assert summary.gate_stats.get("rejected_reason_top")
    assert any("event_gate_pass_total=" in rec.message for rec in caplog.records)
