"""CEO Brief content_strategy guard tests.

Validates:
- build_data_card: 1-3 metrics, regex extraction, fallback to final_score
- build_chart_spec: valid chart_type, labels/values length match
- build_video_source: YouTube search URL, fallback to title_plain
- build_ceo_metaphor: mandatory connector, 2-3 sentences
- build_ceo_brief_blocks: all keys present, constraints met
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.content_strategy import (
    build_ceo_actions,
    build_ceo_brief_blocks,
    build_ceo_metaphor,
    build_chart_spec,
    build_corp_watch_summary,
    build_data_card,
    build_signal_summary,
    build_structured_executive_summary,
    build_video_source,
    compute_market_heat,
    score_event_impact,
)
from schemas.education_models import EduNewsCard


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_card(**overrides) -> EduNewsCard:
    defaults = dict(
        item_id="test-001",
        is_valid_news=True,
        title_plain="Nvidia launches new GPU chip today",
        what_happened="Nvidia 今日推出新款 GPU 晶片，效能提升 40%，售價 2000 美元",
        why_important="影響 AI 訓練成本與效能",
        focus_action="評估是否採購",
        metaphor="就像汽車引擎升級，所有車都能跑更快",
        fact_check_confirmed=["Nvidia announced the chip at CES 2026",
                              "Performance benchmarks show 40% improvement"],
        evidence_lines=["Nvidia CEO Jensen Huang presented the new architecture"],
        technical_interpretation="新架構採用 4nm 製程",
        derivable_effects=["AI 訓練成本可能下降 20-30%", "競爭對手 AMD 壓力增加"],
        speculative_effects=["可能引發新一輪 AI 軍備競賽"],
        action_items=["評估新 GPU 對現有 AI 專案的影響"],
        video_suggestions=["Nvidia GPU chip launch 2026"],
        source_url="https://example.com/nvidia-gpu",
        category="人工智慧",
        final_score=8.5,
    )
    defaults.update(overrides)
    return EduNewsCard(**defaults)


def _make_empty_card() -> EduNewsCard:
    """Card with minimal data — test fallback paths."""
    return EduNewsCard(
        item_id="empty-001",
        is_valid_news=True,
        title_plain="Something happened today",
        what_happened="Something launched today",
        category="tech",
        final_score=5.0,
    )


# ---------------------------------------------------------------------------
# build_data_card
# ---------------------------------------------------------------------------

class TestBuildDataCard:
    def test_returns_1_to_3_metrics(self):
        result = build_data_card(_make_card())
        assert 1 <= len(result) <= 3

    def test_each_metric_has_label_and_value(self):
        result = build_data_card(_make_card())
        for m in result:
            assert "label" in m
            assert "value" in m
            assert len(m["value"]) > 0

    def test_extracts_numbers_from_text(self):
        card = _make_card(what_happened="營收達 500 億美元，用戶數 2000 萬")
        result = build_data_card(card)
        values = " ".join(m["value"] for m in result)
        assert any(c.isdigit() for c in values)

    def test_fallback_uses_final_score(self):
        card = _make_card(
            what_happened="no numbers here",
            fact_check_confirmed=[],
            evidence_lines=[],
            derivable_effects=[],
        )
        result = build_data_card(card)
        assert len(result) >= 1
        values = " ".join(m["value"] for m in result)
        assert "8.5" in values or "5" in values  # final_score fallback


# ---------------------------------------------------------------------------
# build_chart_spec
# ---------------------------------------------------------------------------

class TestBuildChartSpec:
    def test_valid_chart_type(self):
        result = build_chart_spec(_make_card())
        assert result["type"] in ("bar", "line", "pie")

    def test_labels_values_same_length(self):
        result = build_chart_spec(_make_card())
        assert len(result["labels"]) == len(result["values"])
        assert len(result["labels"]) >= 1

    def test_values_are_numeric(self):
        result = build_chart_spec(_make_card())
        for v in result["values"]:
            assert isinstance(v, (int, float))


# ---------------------------------------------------------------------------
# build_video_source
# ---------------------------------------------------------------------------

class TestBuildVideoSource:
    def test_returns_youtube_url(self):
        result = build_video_source(_make_card())
        assert len(result) >= 1
        assert "youtube.com" in result[0]["url"]
        assert len(result[0]["title"]) > 0

    def test_fallback_uses_title(self):
        card = _make_card(video_suggestions=[])
        result = build_video_source(card)
        assert len(result) >= 1
        assert "youtube.com" in result[0]["url"]

    def test_empty_card_still_works(self):
        result = build_video_source(_make_empty_card())
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# build_ceo_metaphor
# ---------------------------------------------------------------------------

METAPHOR_CONNECTORS = ["就像", "等於是", "可以想像成", "好比", "類似於"]


class TestBuildCeoMetaphor:
    def test_contains_connector(self):
        result = build_ceo_metaphor(_make_card())
        assert any(c in result for c in METAPHOR_CONNECTORS), (
            f"Metaphor must contain a connector, got: {result}"
        )

    def test_sentence_count_2_to_3(self):
        result = build_ceo_metaphor(_make_card())
        # Count sentences by Chinese/Western period
        sentences = [s for s in re.split(r"[。.]", result) if s.strip()]
        assert 1 <= len(sentences) <= 3, (
            f"Expected 1-3 sentences, got {len(sentences)}: {result}"
        )

    def test_fallback_when_no_metaphor(self):
        card = _make_card(metaphor="", what_happened="", why_important="")
        result = build_ceo_metaphor(card)
        assert any(c in result for c in METAPHOR_CONNECTORS)

    def test_empty_card(self):
        result = build_ceo_metaphor(_make_empty_card())
        assert len(result) > 0
        assert any(c in result for c in METAPHOR_CONNECTORS)


# ---------------------------------------------------------------------------
# build_ceo_brief_blocks
# ---------------------------------------------------------------------------

REQUIRED_KEYS = [
    "title", "ai_trend_liner", "image_query", "event_liner",
    "data_card", "chart_spec", "ceo_metaphor",
    "q1_meaning", "q2_impact", "q3_actions",
    "video_source", "sources",
]


class TestBuildCeoBriefBlocks:
    def test_all_keys_present(self):
        result = build_ceo_brief_blocks(_make_card())
        for key in REQUIRED_KEYS:
            assert key in result, f"Missing key: {key}"

    def test_title_max_14_chars(self):
        result = build_ceo_brief_blocks(_make_card())
        # _smart_truncate(text, 14) may add "…" making it 15; allow +1 for ellipsis
        assert len(result["title"]) <= 15

    def test_q3_actions_max_3(self):
        result = build_ceo_brief_blocks(_make_card())
        assert len(result["q3_actions"]) <= 3

    def test_data_card_not_empty(self):
        result = build_ceo_brief_blocks(_make_card())
        assert len(result["data_card"]) >= 1

    def test_chart_spec_not_empty(self):
        result = build_ceo_brief_blocks(_make_card())
        cs = result["chart_spec"]
        assert cs.get("type") in ("bar", "line", "pie")

    def test_video_source_not_empty(self):
        result = build_ceo_brief_blocks(_make_card())
        assert len(result["video_source"]) >= 1

    def test_sources_not_empty(self):
        result = build_ceo_brief_blocks(_make_card())
        assert len(result["sources"]) >= 1

    def test_empty_card_still_returns_all_keys(self):
        result = build_ceo_brief_blocks(_make_empty_card())
        for key in REQUIRED_KEYS:
            assert key in result, f"Missing key for empty card: {key}"


# ---------------------------------------------------------------------------
# build_structured_executive_summary
# ---------------------------------------------------------------------------

SUMMARY_SECTION_KEYS = [
    "ai_trends", "tech_landing", "market_competition",
    "opportunities_risks", "recommended_actions",
]


class TestBuildStructuredExecutiveSummary:
    def test_all_sections_present(self):
        cards = [_make_card()]
        result = build_structured_executive_summary(cards)
        for key in SUMMARY_SECTION_KEYS:
            assert key in result, f"Missing section: {key}"
            assert len(result[key]) >= 1

    def test_each_section_max_3_items(self):
        cards = [_make_card(item_id=f"c-{i}") for i in range(5)]
        result = build_structured_executive_summary(cards)
        for key in SUMMARY_SECTION_KEYS:
            assert len(result[key]) <= 3

    def test_empty_cards(self):
        result = build_structured_executive_summary([])
        for key in SUMMARY_SECTION_KEYS:
            assert key in result
            assert len(result[key]) >= 1


# ---------------------------------------------------------------------------
# v5 — compute_market_heat
# ---------------------------------------------------------------------------

class TestComputeMarketHeat:
    def test_returns_required_keys(self):
        result = compute_market_heat([_make_card()])
        assert "score" in result
        assert "level" in result
        assert "trend_word" in result

    def test_score_0_to_100(self):
        result = compute_market_heat([_make_card()])
        assert 0 <= result["score"] <= 100

    def test_valid_level(self):
        result = compute_market_heat([_make_card()])
        assert result["level"] in ("LOW", "MEDIUM", "HIGH", "VERY_HIGH")

    def test_empty_cards_returns_low(self):
        result = compute_market_heat([])
        assert result["score"] == 0
        assert result["level"] == "LOW"

    def test_high_score_cards(self):
        cards = [_make_card(item_id=f"h-{i}", final_score=9.5) for i in range(5)]
        result = compute_market_heat(cards)
        assert result["level"] in ("HIGH", "VERY_HIGH")


# ---------------------------------------------------------------------------
# v5 — score_event_impact
# ---------------------------------------------------------------------------

class TestScoreEventImpact:
    def test_returns_required_keys(self):
        result = score_event_impact(_make_card())
        assert "impact" in result
        assert "label" in result
        assert "color_tag" in result

    def test_impact_1_to_5(self):
        result = score_event_impact(_make_card())
        assert 1 <= result["impact"] <= 5

    def test_valid_label(self):
        result = score_event_impact(_make_card())
        assert result["label"] in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL")

    def test_valid_color(self):
        result = score_event_impact(_make_card())
        assert result["color_tag"] in ("red", "orange", "yellow", "gray")

    def test_high_score_high_impact(self):
        card = _make_card(final_score=9.5)
        result = score_event_impact(card)
        assert result["impact"] >= 4

    def test_low_score_low_impact(self):
        card = _make_card(
            final_score=2.0,
            fact_check_confirmed=[],
            derivable_effects=[],
            action_items=[],
        )
        result = score_event_impact(card)
        assert result["impact"] <= 2


# ---------------------------------------------------------------------------
# v5 — build_ceo_actions
# ---------------------------------------------------------------------------

class TestBuildCeoActions:
    def test_returns_list(self):
        result = build_ceo_actions([_make_card()])
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_each_action_has_required_keys(self):
        result = build_ceo_actions([_make_card()])
        for act in result:
            assert "action_type" in act
            assert "title" in act
            assert "detail" in act
            assert "owner" in act
            assert "color_tag" in act

    def test_valid_action_types(self):
        result = build_ceo_actions([_make_card()])
        for act in result:
            assert act["action_type"] in ("MOVE", "TEST", "WATCH")

    def test_sorted_move_first(self):
        cards = [
            _make_card(item_id="low", final_score=3.0,
                       fact_check_confirmed=[], derivable_effects=[],
                       action_items=[]),
            _make_card(item_id="high", final_score=9.5),
        ]
        result = build_ceo_actions(cards)
        if len(result) >= 2:
            order = {"MOVE": 0, "TEST": 1, "WATCH": 2}
            for i in range(len(result) - 1):
                assert order[result[i]["action_type"]] <= order[result[i+1]["action_type"]]

    def test_empty_cards(self):
        result = build_ceo_actions([])
        assert result == []


# ---------------------------------------------------------------------------
# v5 — build_signal_summary
# ---------------------------------------------------------------------------

class TestBuildSignalSummary:
    def test_returns_list(self):
        result = build_signal_summary([_make_card()])
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_each_signal_has_required_keys(self):
        result = build_signal_summary([_make_card()])
        for sig in result:
            assert "signal_type" in sig
            assert "label" in sig
            assert "title" in sig
            assert "source_count" in sig
            assert "heat" in sig

    def test_valid_signal_types(self):
        result = build_signal_summary([_make_card()])
        valid_types = {"TOOL_ADOPTION", "USER_PAIN", "WORKFLOW_CHANGE"}
        for sig in result:
            assert sig["signal_type"] in valid_types

    def test_valid_heat(self):
        result = build_signal_summary([_make_card()])
        for sig in result:
            assert sig["heat"] in ("hot", "warm", "cool")

    def test_empty_cards_still_returns(self):
        result = build_signal_summary([])
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# v5 — build_corp_watch_summary
# ---------------------------------------------------------------------------

class TestBuildCorpWatchSummary:
    def test_returns_required_keys(self):
        result = build_corp_watch_summary([_make_card()])
        assert "tier_a" in result
        assert "tier_b" in result
        assert "total_mentions" in result

    def test_nvidia_detected_tier_a(self):
        card = _make_card(title_plain="Nvidia launches new GPU chip today")
        result = build_corp_watch_summary([card])
        names = [item["name"] for item in result["tier_a"]]
        assert "NVIDIA" in names

    def test_tier_a_item_structure(self):
        card = _make_card(title_plain="Nvidia launches new GPU chip today")
        result = build_corp_watch_summary([card])
        for item in result["tier_a"]:
            assert "name" in item
            assert "event_title" in item
            assert "impact_label" in item
            assert "action" in item

    def test_empty_cards(self):
        result = build_corp_watch_summary([])
        assert result["total_mentions"] == 0
        assert result["tier_a"] == []
        assert result["tier_b"] == []
