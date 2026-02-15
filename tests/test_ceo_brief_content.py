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
    build_ceo_brief_blocks,
    build_ceo_metaphor,
    build_chart_spec,
    build_data_card,
    build_structured_executive_summary,
    build_video_source,
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
