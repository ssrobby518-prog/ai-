from __future__ import annotations

import re

from core.content_strategy import build_corp_watch_summary, build_signal_summary
from schemas.education_models import EduNewsCard


def _no_event_cards() -> list[EduNewsCard]:
    return [
        EduNewsCard(
            item_id="signal-001",
            is_valid_news=True,
            title_plain="AgentOps platform workflow update",
            what_happened="AgentOps announced workflow rollout for 12 enterprise teams in 2026 with 35% latency reduction.",
            why_important="Affects cost and model adoption planning.",
            source_name="TechCrunch",
            source_url="https://example.com/agentops-workflow",
            final_score=7.0,
        ),
        EduNewsCard(
            item_id="signal-002",
            is_valid_news=True,
            title_plain="EdgeStack hardware supply update",
            what_happened="EdgeStack reported hardware production schedule for Q2 2026 with 2M unit target.",
            why_important="Impacts infra availability and procurement cadence.",
            source_name="Reuters",
            source_url="https://example.com/edgestack-supply",
            final_score=6.5,
        ),
    ]


def test_no_event_signals_have_density_fields() -> None:
    rows = build_signal_summary(_no_event_cards())
    assert len(rows) >= 3

    for row in rows[:3]:
        assert str(row.get("title", "")).strip()
        assert str(row.get("source_url", "")).startswith("http")
        assert int(row.get("platform_count", 0)) >= 1
        assert int(row.get("heat_score", 0)) >= 30
        assert len(str(row.get("example_snippet", "")).strip()) >= 30
        assert len(str(row.get("example_snippet", ""))) <= 120
        assert isinstance(row.get("evidence_terms"), list)
        assert isinstance(row.get("evidence_numbers"), list)
        assert len(row.get("evidence_terms", [])) >= 2
        assert len(row.get("evidence_numbers", [])) >= 1
        assert "source=unknown" not in str(row.get("signal_text", "")).lower()
        assert "source=platform" not in str(row.get("signal_text", "")).lower()
        assert re.search(r"\b(?:was|is|are|the|and|to|of)\s*$", str(row.get("example_snippet", "")).strip(), re.IGNORECASE) is None


def test_no_event_corp_watch_has_numeric_scan_stats() -> None:
    metrics = {
        "sources_total": 8,
        "sources_success": 6,
        "sources_failed": 2,
        "rejected_reason_top": [("content_too_short", 4), ("rejected_keyword:index", 3)],
    }
    corp = build_corp_watch_summary(_no_event_cards(), metrics=metrics)
    assert int(corp.get("updates", corp.get("total_mentions", 0)) or 0) == 0
    assert int(corp.get("sources_total", 0)) >= 1
    assert int(corp.get("success_count", 0)) >= 0
    assert int(corp.get("fail_count", 0)) >= 0
    assert isinstance(corp.get("top_fail_reasons"), list)
    assert corp.get("top_fail_reasons")
    assert isinstance(corp.get("update_type_counts"), dict)
    assert set(corp["update_type_counts"].keys()) == {"model", "product", "cloud_infra", "ecosystem", "risk_policy"}
    assert isinstance(corp.get("top_sources"), list)
    assert corp.get("top_sources")
    status = str(corp.get("status_message", ""))
    assert "sources_total=" in status
    assert "success_count=" in status
    assert "fail_count=" in status
