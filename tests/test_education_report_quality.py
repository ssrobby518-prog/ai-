"""Education Report Quality Gate — 教育版報告品質守門員。

確保未來任何 PR 都不能破壞教育版格式：
- QA 區塊數量
- 圖片 / 影片 / 心智圖存在
- 語言複雜度限制（技術詞不超標）
- Notion 學習任務存在
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.education_renderer import (
    generate_learning_assets,
    generate_mindmap,
    render_education_report,
    simplify_to_highschool_level,
)
from schemas.models import (
    DeepAnalysisReport,
    ItemDeepDive,
    MergedResult,
    SchemaA,
    SchemaB,
    SchemaC,
)

# ---------------------------------------------------------------------------
# Helper：渲染一份完整報告供測試用
# ---------------------------------------------------------------------------

_METRICS: dict = {
    "run_id": "qg_test_001",
    "total_items": 1,
    "enrich_success_rate": 90.0,
    "enrich_latency_p50": 4.0,
    "enrich_latency_p95": 10.0,
    "entity_noise_removed": 0,
    "total_runtime_seconds": 20.0,
    "enrich_fail_reasons": {},
}


def _render_quality_report() -> str:
    """渲染一份含正常新聞的教育版報告。"""
    result = MergedResult(
        item_id="qg_news_001",
        schema_a=SchemaA(
            item_id="qg_news_001",
            title_zh="Apple 發表全新 AI 晶片 M5 Ultra",
            summary_zh="Apple 在春季發表會上推出 M5 Ultra 晶片，效能提升 40%。",
            category="科技/技術",
            entities=["Apple", "M5 Ultra"],
            key_points=["Apple 推出 M5 Ultra", "效能提升 40%"],
            source_id="https://example.com/apple-m5",
        ),
        schema_b=SchemaB(item_id="qg_news_001", final_score=8.0),
        schema_c=SchemaC(item_id="qg_news_001"),
        passed_gate=True,
    )
    report = DeepAnalysisReport(
        total_items=1,
        per_item_analysis=[
            ItemDeepDive(
                item_id="qg_news_001",
                core_facts=["Apple 推出 M5 Ultra 晶片"],
                evidence_excerpts=["Apple unveiled the M5 Ultra chip"],
                derivable_effects=["筆電效能大幅提升"],
                signal_strength=0.8,
            ),
        ],
    )
    notion_md, _, _ = render_education_report(
        results=[result], report=report, metrics=_METRICS,
    )
    return notion_md


# ---------------------------------------------------------------------------
# Quality Gate 測試
# ---------------------------------------------------------------------------


class TestEducationQualityGate:
    """教育版報告品質守門員。"""

    def setup_method(self):
        self.text = _render_quality_report()

    def test_has_enough_qa_blocks(self):
        """報告必須包含至少 4 個穿插式 QA 區塊。"""
        count = self.text.count("小問答時間")
        assert count >= 4, f"QA 區塊只有 {count} 個，需要至少 4 個"

    def test_contains_image(self):
        """報告必須包含至少 1 張圖片（Unsplash）。"""
        assert "source.unsplash.com" in self.text

    def test_contains_video(self):
        """報告必須包含至少 1 個 YouTube 連結。"""
        assert "youtube.com" in self.text

    def test_contains_mindmap(self):
        """報告必須包含至少 1 個 Mermaid 心智圖。"""
        assert "mindmap" in self.text

    def test_contains_notion_task(self):
        """報告必須包含 Notion 學習任務模板。"""
        assert "Notion 學習任務" in self.text

    def test_contains_next_steps(self):
        """報告必須包含「下一步學習」章節。"""
        assert "下一步學習" in self.text

    def test_language_is_simplified(self):
        """報告中的技術詞數量不可超過 3 個（已被替換的不算）。"""
        forbidden = [
            "Pipeline", "ETL", "Latency", "Renderer",
            "Inference", "LLM",
        ]
        hits = [w for w in forbidden if w in self.text]
        assert len(hits) <= 3, (
            f"技術詞過多（{len(hits)} 個）：{hits}"
        )


# ---------------------------------------------------------------------------
# 獨立函式單元測試
# ---------------------------------------------------------------------------


class TestSimplifyFunction:
    def test_replaces_pipeline(self):
        assert "資料生產線" in simplify_to_highschool_level("Pipeline")

    def test_replaces_etl(self):
        result = simplify_to_highschool_level("ETL process")
        assert "ETL" not in result

    def test_replaces_metrics(self):
        result = simplify_to_highschool_level("Metrics dashboard")
        assert "系統健康數字" in result

    def test_preserves_non_tech_text(self):
        assert simplify_to_highschool_level("你好世界") == "你好世界"


class TestGenerateLearningAssets:
    def test_returns_three_keys(self):
        assets = generate_learning_assets("AI 晶片")
        assert "image_md" in assets
        assert "video_md" in assets
        assert "notion_task_md" in assets

    def test_image_has_unsplash(self):
        assets = generate_learning_assets("test topic")
        assert "unsplash.com" in assets["image_md"]

    def test_video_has_youtube(self):
        assets = generate_learning_assets("test topic")
        assert "youtube.com" in assets["video_md"]


class TestGenerateMindmap:
    def test_has_mermaid_syntax(self):
        mm = generate_mindmap("AI 晶片")
        assert "```mermaid" in mm
        assert "mindmap" in mm

    def test_has_root_node(self):
        mm = generate_mindmap("Apple M5")
        assert "root((Apple M5))" in mm

    def test_has_four_branches(self):
        mm = generate_mindmap("test")
        assert "是什麼" in mm
        assert "為何重要" in mm
        assert "會影響誰" in mm
        assert "下一步" in mm
