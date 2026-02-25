"""Unit tests for utils.evidence_pack.

Pure-Python, no network, no LLM calls.
Tests: compute_ai_relevance, check_no_boilerplate, check_moves_anchored,
       extract_event_anchors, check_q1_structure, check_q2_structure,
       check_exec_readability.
"""

from __future__ import annotations

import pytest

from utils.evidence_pack import (
    AI_KEYWORDS,
    check_exec_readability,
    check_moves_anchored,
    check_no_boilerplate,
    check_q1_structure,
    check_q2_structure,
    compute_ai_relevance,
    extract_event_anchors,
)


# ===========================================================================
# compute_ai_relevance
# ===========================================================================

class TestComputeAiRelevance:
    def test_ai_keyword_in_title(self) -> None:
        assert compute_ai_relevance("OpenAI releases new GPT model", "", "") is True

    def test_ai_keyword_in_quote(self) -> None:
        assert compute_ai_relevance("Company update", "The new LLM benchmark shows gains", "") is True

    def test_no_ai_keyword(self) -> None:
        assert compute_ai_relevance("Quarterly earnings report", "Revenue grew 12%", "Dividends increased") is False

    def test_empty_inputs(self) -> None:
        assert compute_ai_relevance("", "", "") is False

    def test_spec_a2_keywords_fine_tune(self) -> None:
        assert compute_ai_relevance("", "fine-tuning on domain data", "") is True

    def test_spec_a2_keywords_benchmark(self) -> None:
        assert compute_ai_relevance("New benchmark results released", "", "") is True

    def test_spec_a2_keywords_hallucin(self) -> None:
        # prefix match: hallucination
        assert compute_ai_relevance("", "reduces hallucination rate", "") is True

    def test_spec_a2_keywords_hugging_face(self) -> None:
        assert compute_ai_relevance("", "Hugging Face releases new model hub", "") is True

    def test_spec_a2_keywords_cuda(self) -> None:
        assert compute_ai_relevance("", "CUDA 12.4 optimization improves inference", "") is True

    def test_spec_a2_keywords_autonomous(self) -> None:
        assert compute_ai_relevance("", "autonomous agent system", "") is True

    def test_anchor_list_contributes(self) -> None:
        # AI keyword is only in the anchor list
        assert compute_ai_relevance("General tech news", "no relevance here", "", anchors=["OpenAI"]) is True


# ===========================================================================
# check_no_boilerplate
# ===========================================================================

class TestCheckNoBoilerplate:
    def test_clean_text_passes(self) -> None:
        q1 = "NVIDIA公布H200進展，原文記載：「H200 GPU with 141GB HBM3e」，此訊息來源可直接核實。"
        q2 = "此次NVIDIA進展對AI領域具體影響，原文顯示：「2x memory bandwidth vs H100」，可據此布局評估。"
        ok, reasons = check_no_boilerplate(q1, q2)
        assert ok
        assert reasons == []

    def test_zuixin_gonggao_banned(self) -> None:
        q1 = "OpenAI最新公告顯示相關技術或產品有具體進展，原文直述：「test window」。"
        ok, reasons = check_no_boilerplate(q1, "some clean q2")
        assert not ok
        assert any("最新公告顯示" in r for r in reasons)

    def test_yuanwen_yiti_banned(self) -> None:
        q2 = "此次進展原文已提供具體文字依據：「window」，決策者可評估。"
        ok, reasons = check_no_boilerplate("clean q1", q2)
        assert not ok
        assert any("原文已提供" in r for r in reasons)

    def test_bimian_tuice_banned(self) -> None:
        q2 = "請避免基於推測作出判斷。"
        ok, reasons = check_no_boilerplate("clean q1", q2)
        assert not ok
        assert any("避免基於推測" in r for r in reasons)

    def test_yinfa_taolun_banned(self) -> None:
        q1 = "此次發布引發廣泛討論，值得關注。"
        ok, reasons = check_no_boilerplate(q1, "clean q2")
        assert not ok
        assert len(reasons) > 0

    def test_empty_strings_pass(self) -> None:
        ok, reasons = check_no_boilerplate("", "")
        assert ok


# ===========================================================================
# check_moves_anchored
# ===========================================================================

class TestCheckMovesAnchored:
    def test_all_anchored_passes(self) -> None:
        moves = ["OpenAI：確認原始來源與版本時間戳。", "OpenAI：定義可量測 KPI。"]
        risks = ["OpenAI相關訊號可能反轉，需保留調整空間。"]
        ok, reasons = check_moves_anchored(moves, risks, ["OpenAI"])
        assert ok
        assert reasons == []

    def test_unanchored_bullet_fails(self) -> None:
        moves = ["確認原始來源與版本時間戳。"]  # No anchor
        risks = ["訊號可能反轉。"]               # No anchor
        ok, reasons = check_moves_anchored(moves, risks, ["OpenAI"])
        assert not ok
        assert len(reasons) >= 2

    def test_no_anchors_passes(self) -> None:
        # No anchors available → cannot enforce → pass
        moves = ["Some generic bullet."]
        ok, reasons = check_moves_anchored(moves, [], [])
        assert ok

    def test_partial_anchoring(self) -> None:
        moves = ["OpenAI：check data.", "random generic line"]
        risks = ["OpenAI risk: token costs"]
        anchors = ["OpenAI"]
        ok, reasons = check_moves_anchored(moves, risks, anchors)
        assert not ok
        # Only the unanchored bullet should fail
        assert len(reasons) == 1
        assert "random generic line" in reasons[0]

    def test_case_insensitive_anchor_match(self) -> None:
        moves = ["openai announced new model"]
        ok, reasons = check_moves_anchored(moves, [], ["OpenAI"])
        assert ok


# ===========================================================================
# extract_event_anchors
# ===========================================================================

class TestExtractEventAnchors:
    def test_company_extracted(self) -> None:
        anchors = extract_event_anchors("OpenAI releases GPT-5", "OpenAI CEO says", "")
        assert "OpenAI" in anchors

    def test_version_extracted(self) -> None:
        anchors = extract_event_anchors("Claude 3.7 sonnet released", "", "")
        # Should find 3.7 or similar version
        has_version = any("3.7" in a or "Claude" in a for a in anchors)
        assert has_version

    def test_percentage_extracted(self) -> None:
        anchors = extract_event_anchors("", "Achieves 95% accuracy benchmark", "")
        has_pct = any("95" in a for a in anchors)
        assert has_pct

    def test_n_limit_respected(self) -> None:
        anchors = extract_event_anchors(
            "OpenAI Microsoft Google NVIDIA Meta",
            "benchmarks 1.5 2.3 v4.1",
            "",
            n=3,
        )
        assert len(anchors) <= 3

    def test_empty_returns_empty(self) -> None:
        anchors = extract_event_anchors("", "", "")
        assert isinstance(anchors, list)


# ===========================================================================
# check_q1_structure
# ===========================================================================

class TestCheckQ1Structure:
    LQ = "\u300c"
    RQ = "\u300d"

    def test_valid_q1_passes(self) -> None:
        actor = "OpenAI"
        q1_zh = (
            f"OpenAI公布新進展，原文記載：{self.LQ}releases GPT-4o with vision{self.RQ}，"
            f"此訊息來源可直接核實，供決策者查閱評估。"
        )
        quote_1 = "releases GPT-4o with vision capabilities for all users"
        anchors = ["OpenAI"]
        ok, reasons = check_q1_structure(q1_zh, actor, quote_1, anchors)
        assert ok, f"Expected PASS but got: {reasons}"

    def test_missing_quote_window_fails(self) -> None:
        actor = "NVIDIA"
        q1_zh = "NVIDIA公布H200進展，此訊息來源可直接核實。全球AI算力提升。效能大幅改善。"
        ok, reasons = check_q1_structure(q1_zh, actor, "H200 GPU announced", ["NVIDIA"])
        assert not ok
        assert "Q1_NO_QUOTE_WINDOW" in reasons

    def test_no_actor_no_anchor_fails(self) -> None:
        q1_zh = f"公司公布進展，原文記載：{self.LQ}some window here{self.RQ}，此訊息可核實。亦可查閱原始資料。"
        ok, reasons = check_q1_structure(q1_zh, "SomeActorNotPresent", "quote here", [])
        assert not ok
        assert "Q1_NO_ACTOR_OR_ANCHOR" in reasons


# ===========================================================================
# check_q2_structure
# ===========================================================================

class TestCheckQ2Structure:
    LQ = "\u300c"
    RQ = "\u300d"

    def test_valid_q2_passes(self) -> None:
        q2_zh = (
            f"此次Anthropic進展對AI領域具體影響，原文顯示：{self.LQ}2x faster inference speed{self.RQ}，"
            f"可據此布局評估，相關業者可直接核對查閱。"
        )
        quote_2 = "2x faster inference speed compared to previous model"
        anchors = ["Anthropic"]
        ok, reasons = check_q2_structure(q2_zh, quote_2, anchors)
        assert ok, f"Expected PASS but got: {reasons}"

    def test_missing_window_fails(self) -> None:
        q2_zh = "此次進展對AI領域影響顯著。評估效益。規劃布局。追蹤後續。"
        ok, reasons = check_q2_structure(q2_zh, "some quote", ["Anthropic"])
        assert not ok
        assert "Q2_NO_QUOTE_WINDOW" in reasons


# ===========================================================================
# check_exec_readability
# ===========================================================================

class TestCheckExecReadability:
    LQ = "\u300c"
    RQ = "\u300d"

    def test_clean_card_passes(self) -> None:
        q1_zh = (
            f"NVIDIA公布H200進展，原文記載：{self.LQ}H200 GPU 141GB HBM3e{self.RQ}，"
            f"此訊息可核實，效能大幅提升。"
        )
        q2_zh = (
            f"此次NVIDIA進展對AI算力具體影響，原文顯示：{self.LQ}2x memory bandwidth{self.RQ}，"
            f"可據此布局評估，相關業者可直接核對。"
        )
        ok, reasons = check_exec_readability(q1_zh, q2_zh, "NVIDIA", "H200 GPU 141GB HBM3e", "2x memory bandwidth")
        assert ok, f"Expected PASS but got: {reasons}"

    def test_boilerplate_fails(self) -> None:
        q1_zh = (
            f"OpenAI最新公告顯示技術進展，原文記載：{self.LQ}window here{self.RQ}，"
            f"可核實，各方持續追蹤後續發展動向。"
        )
        q2_zh = (
            f"此次進展影響顯著，原文顯示：{self.LQ}window2{self.RQ}，"
            f"可據此布局評估，相關業者查閱。"
        )
        ok, reasons = check_exec_readability(q1_zh, q2_zh, "OpenAI", "window here", "window2")
        assert not ok

    def test_missing_window_fails(self) -> None:
        q1_zh = "OpenAI公布新進展，此訊息可核實。技術大幅提升，效能改善顯著。整體布局評估完成。"
        q2_zh = (
            f"此次進展影響顯著，原文顯示：{self.LQ}window2{self.RQ}，"
            f"可據此布局評估，更多細節可查閱。"
        )
        ok, reasons = check_exec_readability(q1_zh, q2_zh, "OpenAI", "window1_not_here", "window2")
        assert not ok
        assert "Q1_WINDOW_MISSING" in reasons


# ===========================================================================
# AI_KEYWORDS list sanity
# ===========================================================================

def test_ai_keywords_contains_spec_a2_items() -> None:
    """Verify spec A2 required keywords are in AI_KEYWORDS."""
    required = ["fine-tune", "benchmark", "CUDA", "reasoning", "autonomous", "Hugging Face"]
    for kw in required:
        assert kw in AI_KEYWORDS, f"Missing keyword: {kw}"


def test_ai_keywords_has_hallucin_prefix() -> None:
    """hallucin prefix must be in AI_KEYWORDS for prefix matching."""
    assert "hallucin" in AI_KEYWORDS
