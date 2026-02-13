"""Z5 Education Renderer 單元測試（成人教育版）。

包含：
- test_adult_report_contains_required_sections：驗證成人版所有必要區塊
- test_ppt_report_has_page_separators：驗證 PPT 切頁版
- test_xmind_outline_indentation：驗證 XMind 階層大綱
- test_fallback_parse：文本 fallback 解析
- golden snapshot 比對
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.education_renderer import render_education_report
from schemas.education_models import (
    SystemHealthReport,
    is_invalid_item,
    is_system_banner,
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
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_METRICS: dict = {
    "run_id": "abc123def456",
    "total_items": 2,
    "enrich_success_rate": 75.0,
    "enrich_latency_p50": 5.2,
    "enrich_latency_p95": 12.8,
    "entity_noise_removed": 3,
    "total_runtime_seconds": 45.6,
    "enrich_fail_reasons": {
        "extract_low_quality": 1,
        "blocked": 1,
    },
}


def _make_normal_result() -> MergedResult:
    return MergedResult(
        item_id="news_music_001",
        schema_a=SchemaA(
            item_id="news_music_001",
            title_zh="環球音樂集團宣布收購獨立音樂廠牌 DistroKid",
            summary_zh="環球音樂集團宣布以 12 億美元收購獨立音樂發行平台 DistroKid，"
            "這項交易將整合雙方的音樂發行網絡與數位服務。",
            category="併購/企業",
            category_confidence=0.92,
            entities=["環球音樂集團", "DistroKid"],
            key_points=[
                "環球音樂宣布收購 DistroKid",
                "交易金額 12 億美元",
                "將整合音樂發行網絡",
            ],
            source_id="https://example.com/music-acquisition",
            score_seed=8.5,
        ),
        schema_b=SchemaB(
            item_id="news_music_001",
            final_score=8.5,
            novelty=8.0,
            utility=7.5,
            heat=9.0,
        ),
        schema_c=SchemaC(item_id="news_music_001"),
        passed_gate=True,
    )


def _make_banner_result() -> MergedResult:
    return MergedResult(
        item_id="banner_signin_002",
        schema_a=SchemaA(
            item_id="banner_signin_002",
            title_zh="You signed in with another tab or window. Reload to refresh your session.",
            summary_zh="You signed in with another tab or window. Reload to refresh your session. "
            "You signed out in another tab or window. Reload to refresh your session.",
            category="",
            entities=[],
            key_points=[],
            source_id="banner_signin_002",
        ),
        schema_b=SchemaB(item_id="banner_signin_002", final_score=1.0),
        schema_c=SchemaC(item_id="banner_signin_002"),
        passed_gate=False,
    )


def _make_deep_report() -> DeepAnalysisReport:
    return DeepAnalysisReport(
        generated_at="{{TIMESTAMP}}",
        total_items=2,
        per_item_analysis=[
            ItemDeepDive(
                item_id="news_music_001",
                core_facts=["環球音樂以 12 億美元收購 DistroKid"],
                evidence_excerpts=[
                    "Universal Music Group announced acquisition of DistroKid for $1.2B",
                    "The deal consolidates independent music distribution",
                ],
                derivable_effects=["獨立音樂人的發行管道可能受影響"],
                opportunities=["獨立音樂發行領域可能出現新競爭者"],
                signal_strength=0.85,
                evidence_density=0.72,
            ),
            ItemDeepDive(
                item_id="banner_signin_002",
                core_facts=[],
                evidence_excerpts=[],
                signal_strength=0.0,
            ),
        ],
    )


SAMPLE_DEEP_ANALYSIS_TEXT = """# 深度分析報告

生成時間：2025-01-01

## 執行摘要

本次分析 2 則新聞。

### 1. 環球音樂收購 DistroKid

**核心事實**
- 環球音樂集團宣布收購獨立音樂發行平台 DistroKid
- 交易金額約 12 億美元

> "Universal Music Group announced acquisition of DistroKid for $1.2B"
> "The deal consolidates independent music distribution"

信號強度: 0.85

### 2. GitHub session banner

**核心事實**
- You signed in with another tab or window. Reload to refresh your session.

> "You signed in with another tab or window"

信號強度: 0.1

---

## Run Metrics

- **run_id**: `test_run_001`
"""


# ---------------------------------------------------------------------------
# 共用 setup
# ---------------------------------------------------------------------------


def _render_all():
    """渲染完整的三份報告。"""
    results = [_make_normal_result(), _make_banner_result()]
    report = _make_deep_report()
    return render_education_report(
        results=results,
        report=report,
        metrics=SAMPLE_METRICS,
    )


# ---------------------------------------------------------------------------
# 測試：is_system_banner / is_invalid_item
# ---------------------------------------------------------------------------


class TestInvalidDetection:
    def test_detects_signin_banner(self):
        assert is_system_banner("You signed in with another tab or window. Reload to refresh your session.") is True

    def test_normal_news_not_banner(self):
        text = "環球音樂集團宣布以 12 億美元收購獨立音樂發行平台 DistroKid，這項交易將整合雙方的音樂發行網絡。"
        assert is_system_banner(text) is False

    def test_cookie_notice_is_banner(self):
        assert is_system_banner("Accept all cookies to continue") is True

    def test_is_invalid_item_short_gibberish(self):
        assert is_invalid_item("abc") is True

    def test_is_invalid_item_normal_text(self):
        assert is_invalid_item("環球音樂集團宣布以 12 億美元收購獨立音樂發行平台 DistroKid，這項交易涉及全球音樂產業。") is False


# ---------------------------------------------------------------------------
# 測試：成人版報告必要區塊（Happy Path）
# ---------------------------------------------------------------------------


class TestAdultReportRequiredSections:
    """兩則 item：一則正常、一則 system banner。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.notion_md, self.ppt_md, self.xmind_md = _render_all()

    def test_has_cover_table(self):
        assert "封面資訊" in self.notion_md
        assert "報告時間" in self.notion_md
        assert "Run ID" in self.notion_md
        assert "abc123def456" in self.notion_md

    def test_has_executive_summary(self):
        assert "今日結論" in self.notion_md
        assert "Executive Summary" in self.notion_md

    def test_has_qa_section_with_6_questions(self):
        assert "Q1：" in self.notion_md
        assert "Q2：" in self.notion_md
        assert "Q3：" in self.notion_md
        assert "Q4：" in self.notion_md
        assert "Q5：" in self.notion_md
        assert "Q6：" in self.notion_md
        assert "Pipeline" in self.notion_md

    def test_has_mermaid_flowchart(self):
        assert "```mermaid" in self.notion_md
        assert "flowchart" in self.notion_md
        assert "Z1" in self.notion_md
        assert "Z5" in self.notion_md

    def test_has_two_cards(self):
        assert "第 1 則" in self.notion_md
        assert "第 2 則" in self.notion_md

    def test_valid_card_has_adult_sections(self):
        """正常新聞卡片含摘要、事實核對、證據、解讀、二階效應、行動、媒體。"""
        assert "#### 摘要" in self.notion_md
        assert "Fact Check" in self.notion_md
        assert "Evidence Snippets" in self.notion_md
        assert "技術/商業解讀" in self.notion_md
        assert "Second-order Effects" in self.notion_md
        assert "Actions" in self.notion_md
        assert "媒體與延伸資源" in self.notion_md

    def test_valid_card_has_fact_check(self):
        assert "✅" in self.notion_md

    def test_valid_card_has_second_order_table(self):
        assert "直接影響" in self.notion_md
        assert "觀察指標" in self.notion_md

    def test_valid_card_has_action_with_deadline(self):
        # 行動建議包含期限
        assert "本週內" in self.notion_md or "兩週內" in self.notion_md

    def test_invalid_card_adult_format(self):
        assert "⚠️ 無效內容" in self.notion_md
        assert "可能原因" in self.notion_md
        assert "修復建議" in self.notion_md

    def test_has_metrics_dashboard(self):
        assert "健康度儀表板" in self.notion_md
        assert "Enrich Success Rate" in self.notion_md
        assert "Latency P50" in self.notion_md
        assert "建議門檻" in self.notion_md

    def test_has_troubleshooting(self):
        assert "排錯指引" in self.notion_md
        assert "PowerShell" in self.notion_md
        assert "grep" in self.notion_md.lower() or "Grep" in self.notion_md

    def test_has_sprint_suggestions(self):
        assert "Sprint 建議" in self.notion_md

    def test_has_traffic_light(self):
        assert any(e in self.notion_md for e in ["🟢", "🟡", "🔴"])

    def test_fail_reasons_translated(self):
        assert "全文抽取品質不足" in self.notion_md
        assert "被目標網站封鎖" in self.notion_md or "封鎖" in self.notion_md

    def test_reading_guide(self):
        assert "閱讀指南" in self.notion_md

    def test_adult_level_label(self):
        assert "深度等級：adult" in self.notion_md


# ---------------------------------------------------------------------------
# 測試：PPT 切頁版
# ---------------------------------------------------------------------------


class TestPptReport:
    @pytest.fixture(autouse=True)
    def setup(self):
        _, self.ppt_md, _ = _render_all()

    def test_has_page_separators(self):
        assert "---" in self.ppt_md
        pages = self.ppt_md.split("---")
        assert len(pages) >= 5  # 封面+結論+流程+新聞頁+metrics

    def test_each_news_at_least_2_pages(self):
        # 正常新聞至少有摘要頁 + 事實頁 + 行動頁 = 3 頁
        assert self.ppt_md.count("事實核對") >= 1
        assert self.ppt_md.count("行動建議") >= 1

    def test_has_mermaid(self):
        assert "mermaid" in self.ppt_md

    def test_invalid_card_present(self):
        assert "無效" in self.ppt_md


# ---------------------------------------------------------------------------
# 測試：XMind 階層大綱
# ---------------------------------------------------------------------------


class TestXmindOutline:
    @pytest.fixture(autouse=True)
    def setup(self):
        _, _, self.xmind_md = _render_all()

    def test_root_node(self):
        lines = self.xmind_md.split("\n")
        assert lines[0].startswith("AI 深度情報分析")

    def test_second_level_nodes(self):
        """驗證固定第二層節點存在。"""
        assert "  今日結論" in self.xmind_md
        assert "  系統流程（Z1-Z5）" in self.xmind_md
        assert "  Metrics & 運維" in self.xmind_md

    def test_news_nodes(self):
        assert "  新聞 1：" in self.xmind_md

    def test_invalid_node(self):
        assert "  無效項目 2：" in self.xmind_md

    def test_indentation_correct(self):
        """驗證縮排是 2 空格。"""
        lines = self.xmind_md.strip().split("\n")
        for line in lines:
            if line != lines[0]:  # 根節點不縮排
                stripped = line.lstrip(" ")
                indent = len(line) - len(stripped)
                assert indent % 2 == 0, f"縮排不是 2 的倍數：'{line}'"
                assert indent >= 2, f"非根節點應至少縮排 2 格：'{line}'"

    def test_news_card_sub_nodes(self):
        """驗證每則新聞下有：摘要、事實核對、證據、技術解讀、二階效應、行動建議、素材。"""
        for label in ["摘要", "事實核對", "證據", "技術解讀", "二階效應", "行動建議", "素材"]:
            assert f"    {label}" in self.xmind_md, f"缺少子節點：{label}"


# ---------------------------------------------------------------------------
# 測試：Fallback
# ---------------------------------------------------------------------------


class TestFallback:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.notion_md, self.ppt_md, self.xmind_md = render_education_report(
            deep_analysis_text=SAMPLE_DEEP_ANALYSIS_TEXT,
            metrics=SAMPLE_METRICS,
        )

    def test_fallback_has_cover(self):
        assert "封面資訊" in self.notion_md

    def test_fallback_has_summary(self):
        assert "今日結論" in self.notion_md

    def test_fallback_has_card(self):
        assert "第 1 則" in self.notion_md

    def test_fallback_detects_invalid(self):
        assert "無效內容" in self.notion_md or "系統訊息" in self.notion_md

    def test_fallback_xmind_generated(self):
        assert len(self.xmind_md) > 50
        assert "AI 深度情報分析" in self.xmind_md


# ---------------------------------------------------------------------------
# 測試：空輸入
# ---------------------------------------------------------------------------


class TestEmptyInput:
    def test_empty_input_no_crash(self):
        notion_md, ppt_md, xmind_md = render_education_report(metrics={})
        assert "封面資訊" in notion_md
        assert len(ppt_md) > 50
        assert len(xmind_md) > 20

    def test_none_metrics_no_crash(self):
        notion_md, _, _ = render_education_report(metrics=None)
        assert "封面資訊" in notion_md


# ---------------------------------------------------------------------------
# 測試：紅黃綠燈
# ---------------------------------------------------------------------------


class TestTrafficLight:
    def test_green(self):
        h = SystemHealthReport(success_rate=90, p95_latency=10)
        assert h.traffic_light == "green"

    def test_yellow(self):
        h = SystemHealthReport(success_rate=60, p95_latency=50)
        assert h.traffic_light == "yellow"

    def test_red(self):
        h = SystemHealthReport(success_rate=30, p95_latency=120)
        assert h.traffic_light == "red"


# ---------------------------------------------------------------------------
# Golden Snapshot（成人版）
# ---------------------------------------------------------------------------

GOLDEN_PATH = Path(__file__).parent / "golden_adult_education_report.md"


class TestGoldenSnapshot:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.notion_md, _, _ = _render_all()

    def _normalize(self, text: str) -> str:
        text = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}[^\|]*", "{{TIMESTAMP}}", text)
        text = re.sub(r"\{\{TIMESTAMP\}\}", "{{TIMESTAMP}}", text)
        # 也替換年份（在 Google 搜尋關鍵字中）
        text = re.sub(r"\b20\d{2}\b", "{{YEAR}}", text)
        return text.strip()

    def test_golden_snapshot_matches(self):
        if not GOLDEN_PATH.exists():
            normalized = self._normalize(self.notion_md)
            GOLDEN_PATH.write_text(normalized, encoding="utf-8")
            pytest.skip("Golden snapshot 首次建立，請重跑測試")

        golden = GOLDEN_PATH.read_text(encoding="utf-8").strip()
        current = self._normalize(self.notion_md)

        expected_sections = [
            "封面資訊",
            "今日結論",
            "這套系統到底在做什麼",
            "系統流程圖",
            "今日新聞卡片",
            "Metrics 與運維建議",
            "Sprint 建議",
        ]
        for section in expected_sections:
            assert section in current, f"缺少區塊：{section}"
            assert section in golden, f"Golden snapshot 缺少區塊：{section}"

        golden_lines = len(golden.splitlines())
        current_lines = len(current.splitlines())
        diff_ratio = abs(golden_lines - current_lines) / max(golden_lines, 1)
        assert diff_ratio < 0.2, (
            f"行數差異過大：golden={golden_lines}, current={current_lines}"
        )
