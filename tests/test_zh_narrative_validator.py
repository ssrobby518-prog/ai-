"""Unit tests for utils.zh_narrative_validator.validate_zh_card_fields.

These tests are pure-Python — no network, no pipeline, no LLM calls.
They verify the machine-checkable rules defined in ZH_NARRATIVE_SPEC.
"""

from __future__ import annotations

import pytest

from utils.zh_narrative_validator import (
    FULLWIDTH_LEFT_BRACKET as LQ,
    FULLWIDTH_RIGHT_BRACKET as RQ,
    validate_zh_card_fields,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_q1zh(actor: str, window: str) -> str:
    """Minimal valid q1_zh template (v2 evidence-driven, no banned phrases)."""
    return (
        f"{actor}公布新進展，"
        f"原文記載：{LQ}{window}{RQ}，"
        f"此訊息來源可直接核實，供決策者查閱評估。"
        f"市場動態持續關注中，後續影響值得評估。"
    )


def _make_q2zh(actor: str, window: str) -> str:
    """Minimal valid q2_zh template (v2 evidence-driven, no banned phrases)."""
    return (
        f"此次{actor}進展對AI領域具體影響，"
        f"原文顯示：{LQ}{window}{RQ}，"
        f"可據此布局評估，相關業者可直接核對查閱。"
        f"後續技術指標值得持續追蹤。"
    )


# ---------------------------------------------------------------------------
# Test: well-formed card passes all checks
# ---------------------------------------------------------------------------

def test_valid_pass() -> None:
    """A properly-formed card should pass all validator checks."""
    q1 = "Wayve's self-driving tech has attracted a diverse set of investors"
    q2 = "The result: >5x faster generation with a fundamentally different speed curve"
    qw1 = "diverse set of investors"
    qw2 = ">5x faster generation with"

    q1_zh = _make_q1zh("Wayve", qw1)
    q2_zh = _make_q2zh("Wayve", qw2)

    ok, reasons = validate_zh_card_fields(q1_zh, q2_zh, qw1, qw2, q1, q2)
    assert ok, f"Expected PASS but got reasons: {reasons}"
    assert reasons == []


# ---------------------------------------------------------------------------
# Test: missing quote windows
# ---------------------------------------------------------------------------

def test_missing_quote_windows_fail() -> None:
    """Empty quote_window_1 / quote_window_2 must fail with QW*_EMPTY."""
    q1 = "OpenAI CFO Sarah Friar posted that revenue is on the rise"
    q2 = "OpenAI launched a new platform called OpenAI Frontier for enterprises"

    # Build q_zh without windows so template has 「」 (empty brackets)
    q1_zh = _make_q1zh("OpenAI", "")
    q2_zh = _make_q2zh("OpenAI", "")

    ok, reasons = validate_zh_card_fields(q1_zh, q2_zh, "", "", q1, q2)
    assert not ok
    assert "QW1_EMPTY" in reasons
    assert "QW2_EMPTY" in reasons


# ---------------------------------------------------------------------------
# Test: window present but NOT embedded in q_zh
# ---------------------------------------------------------------------------

def test_window_not_embedded_fail() -> None:
    """quote_window present but 「window」 absent from q_zh → Q*_ZH_NO_WINDOW."""
    q1 = "What a year inside Claude Code taught me about computing"
    q2 = "The shell was left waiting for more input in a pipeline"
    qw1 = "year inside Claude Code"
    qw2 = "left waiting for more input"

    # q_zh has the window WITHOUT the fullwidth brackets around it
    q1_zh = (
        f"OpenAI最新公告顯示相關技術或產品有具體進展，"
        f"原文直述：{qw1}，"          # <-- missing 「 」 brackets
        f"確認上述訊息有原文出處支撐，可供決策者直接核對查閱。"
    )
    q2_zh = (
        f"此次OpenAI相關發布的後續應用影響，"
        f"原文已提供具體文字依據：{qw2}，"  # <-- missing 「 」 brackets
        f"決策者可依此原始資料評估具體場景的適用性，"
        f"並核查影響範圍，避免基於推測作出判斷。"
    )

    ok, reasons = validate_zh_card_fields(q1_zh, q2_zh, qw1, qw2, q1, q2)
    assert not ok
    assert "Q1_ZH_NO_WINDOW" in reasons
    assert "Q2_ZH_NO_WINDOW" in reasons


# ---------------------------------------------------------------------------
# Test: window modified by transliteration normalisation (the real bug)
# ---------------------------------------------------------------------------

def test_normalised_window_mismatch_fail() -> None:
    """Simulates the root-cause bug: window contains 'Claude' but q_zh has
    the transliteration-normalised form 'Claude（Anthropic）'.  The stored
    window (original) no longer matches 「…」 in q_zh → Q2_ZH_NO_WINDOW.
    """
    q2 = "What a year inside Claude Code taught me about where computing is going"
    qw2 = "What a year inside Claude Code"  # stored verbatim

    # Simulate what the old code produced: _normalize_claude_name applied to
    # the entire q2_zh string changed 'Claude Code' inside 「」.
    q2_zh_broken = (
        f"此次業界相關發布的後續應用影響，"
        f"原文已提供具體文字依據：{LQ}What a year inside Claude（Anthropic） Code{RQ}，"
        f"決策者可依此原始資料評估具體場景的適用性，"
        f"並核查影響範圍，避免基於推測作出判斷。"
    )

    # Any valid q1_zh (not under test here)
    qw1 = "diverse set of investors in the"
    q1 = "Wayve's self-driving tech has attracted a diverse set of investors in the company"
    q1_zh = _make_q1zh("業界", qw1)

    ok, reasons = validate_zh_card_fields(q1_zh, q2_zh_broken, qw1, qw2, q1, q2)
    assert not ok, "Should detect that the stored window no longer matches 「…」 in q2_zh"
    assert "Q2_ZH_NO_WINDOW" in reasons


# ---------------------------------------------------------------------------
# Test: new D2 banned phrases trigger STYLE_SANITY
# ---------------------------------------------------------------------------

def test_new_banned_phrase_zuixin_gonggao() -> None:
    """最新公告顯示 must be caught by the extended _STYLE_SANITY_RE."""
    q1 = "OpenAI launched a new flagship model"
    q2 = "The result: faster generation with 5x improvement"
    qw1 = "launched a new flagship"
    qw2 = "faster generation with 5x"

    # Build q1_zh that contains the old banned template phrase
    q1_zh_bad = (
        f"OpenAI最新公告顯示相關技術或產品有具體進展，"
        f"原文直述：{LQ}{qw1}{RQ}，"
        f"確認上述訊息有原文出處支撐，可供決策者直接核對查閱。"
    )
    q2_zh = _make_q2zh("OpenAI", qw2)

    ok, reasons = validate_zh_card_fields(q1_zh_bad, q2_zh, qw1, qw2, q1, q2)
    assert not ok, "最新公告顯示 should trigger STYLE_SANITY fail"
    assert "STYLE_SANITY" in reasons


def test_new_banned_phrase_queren_chuyuan() -> None:
    """確認.*原文出處 must be caught by the extended _STYLE_SANITY_RE."""
    q1 = "Anthropic released Claude 3.7 with extended context"
    q2 = "Claude 3.7 supports 200k token context windows"
    qw1 = "released Claude 3.7 with"
    qw2 = "supports 200k token context"

    q1_zh_bad = (
        f"Anthropic發布新模型，"
        f"原文直述：{LQ}{qw1}{RQ}，"
        f"確認原文出處已核實，可供決策者查閱。"
    )
    q2_zh = _make_q2zh("Anthropic", qw2)

    ok, reasons = validate_zh_card_fields(q1_zh_bad, q2_zh, qw1, qw2, q1, q2)
    assert not ok, "確認.*原文出處 should trigger STYLE_SANITY fail"
    assert "STYLE_SANITY" in reasons


def test_new_banned_phrase_yuanwen_yiti_yiju() -> None:
    """原文已提供.*依據 must be caught by the extended _STYLE_SANITY_RE."""
    q1 = "Google announced Gemini 2.0 Flash improvements"
    q2 = "Gemini 2.0 brings 2x throughput gains"
    qw1 = "announced Gemini 2.0 Flash"
    qw2 = "brings 2x throughput gains"

    q1_zh = _make_q1zh("Google", qw1)
    q2_zh_bad = (
        f"此次Google相關發布的後續應用影響，"
        f"原文已提供具體文字依據：{LQ}{qw2}{RQ}，"
        f"決策者可依此原始資料評估具體場景的適用性，"
        f"並核查影響範圍。"
    )

    ok, reasons = validate_zh_card_fields(q1_zh, q2_zh_bad, qw1, qw2, q1, q2)
    assert not ok, "原文已提供.*依據 should trigger STYLE_SANITY fail"
    assert "STYLE_SANITY" in reasons


def test_new_banned_phrase_bimian_tuice() -> None:
    """避免基於推測 must be caught by the extended _STYLE_SANITY_RE."""
    q1 = "Microsoft Azure added new AI inference endpoints"
    q2 = "The endpoints cut inference latency by 40%"
    qw1 = "Azure added new AI inference"
    qw2 = "cut inference latency by 40%"

    q1_zh = _make_q1zh("Microsoft", qw1)
    q2_zh_bad = (
        f"此次Microsoft進展對市場影響，"
        f"原文顯示：{LQ}{qw2}{RQ}，"
        f"決策者應避免基於推測作出判斷。"
    )

    ok, reasons = validate_zh_card_fields(q1_zh, q2_zh_bad, qw1, qw2, q1, q2)
    assert not ok, "避免基於推測 should trigger STYLE_SANITY fail"
    assert "STYLE_SANITY" in reasons


def test_clean_v2_template_passes() -> None:
    """The new evidence-driven v2 fallback templates must not trigger any style ban.
    Uses _make_q1zh/_make_q2zh to ensure enough CJK chars (40+) while verifying
    the v2 template phrases are not banned.
    """
    q1 = "NVIDIA released H200 GPU with 141GB HBM3e memory"
    q2 = "H200 delivers 2x memory bandwidth vs H100"
    qw1 = "H200 GPU with 141GB HBM3e"
    qw2 = "2x memory bandwidth vs H100"

    # Use the v2-style templates (no banned phrases) via helper functions
    q1_zh = _make_q1zh("NVIDIA", qw1)
    q2_zh = _make_q2zh("NVIDIA", qw2)

    ok, reasons = validate_zh_card_fields(q1_zh, q2_zh, qw1, qw2, q1, q2)
    assert ok, f"V2 fallback template should pass all style checks but got: {reasons}"
    assert reasons == []


# ---------------------------------------------------------------------------
# Regression: fallback q2_zh in run_once.py must pass Q2_ZH_CHARS (>=40 CJK)
# even when _anchor_for_zh is an all-English company name (0 CJK contribution).
# Previously the 39-char template failed with Q2_ZH_CHARS_LOW for 8 events.
# ---------------------------------------------------------------------------

def test_run_once_q2_fallback_template_zh_chars_pass() -> None:
    """The two-sentence fallback q2_zh template used in run_once.py when
    validate_zh_card_fields fails must itself pass Q2_ZH_CHARS (>=40 CJK).

    Reproduces the failing scenario: English-only anchor → the old 39-char
    template scored Q2_ZH_CHARS_LOW.  The new template guarantees 56 fixed
    CJK chars independent of the variable tokens.
    """
    # Worst-case: all-English actor/anchor — contributes 0 CJK chars
    anchor = "OpenAI"
    qw2 = "new model that outperforms competitors"
    q2 = "The new model that outperforms competitors on all major benchmarks"

    # Reconstruct the new fallback template from run_once.py (lines 1354-1359)
    q2_zh = (
        f"原文「{qw2}」直接揭示本次事件的影響邊界，"
        f"{anchor} 相關部署預計牽動市場結構與產品節奏。"
        f"管理層可依此安排 T+7 核查節點，"
        f"針對 {anchor} 後續指標制定驗證計畫。"
    )

    # Use a valid q1_zh (not under test; just satisfies the validator)
    qw1 = "diverse set of investors in the"
    q1 = "Wayve self-driving tech attracted a diverse set of investors in the company"
    q1_zh = _make_q1zh("業界", qw1)

    ok, reasons = validate_zh_card_fields(q1_zh, q2_zh, qw1, qw2, q1, q2)

    # Q2_ZH_CHARS must NOT fail
    assert "Q2_ZH_CHARS_LOW" not in reasons, (
        f"Fallback q2_zh template has insufficient CJK chars. reasons={reasons}"
    )
    # Q2_ZH_NO_WINDOW must NOT fail (「qw2」 is embedded)
    assert "Q2_ZH_NO_WINDOW" not in reasons, (
        f"Fallback q2_zh template missing 「quote_window_2」. reasons={reasons}"
    )


def test_run_once_q2_fallback_template_no_boilerplate() -> None:
    """Fallback q2_zh template must not trigger NO_BOILERPLATE banned phrases."""
    from utils.evidence_pack import check_no_boilerplate

    anchor = "OpenAI"
    qw2 = "new model that outperforms competitors"
    q2_zh = (
        f"原文「{qw2}」直接揭示本次事件的影響邊界，"
        f"{anchor} 相關部署預計牽動市場結構與產品節奏。"
        f"管理層可依此安排 T+7 核查節點，"
        f"針對 {anchor} 後續指標制定驗證計畫。"
    )

    qw1 = "diverse set of investors in the"
    q1_zh = _make_q1zh("業界", qw1)

    nbp_ok, nbp_reasons = check_no_boilerplate(q1_zh, q2_zh)
    assert nbp_ok, f"Fallback q2_zh triggered NO_BOILERPLATE: {nbp_reasons}"
