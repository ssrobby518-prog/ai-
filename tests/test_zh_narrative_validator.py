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
    """Minimal valid q1_zh template (matches _build_q1_zh_narrative pattern)."""
    return (
        f"{actor}最新公告顯示相關技術或產品有具體進展，"
        f"原文直述：{LQ}{window}{RQ}，"
        f"確認上述訊息有原文出處支撐，"
        f"可供決策者直接核對查閱。"
    )


def _make_q2zh(actor: str, window: str) -> str:
    """Minimal valid q2_zh template (matches _build_q2_zh_narrative pattern)."""
    return (
        f"此次{actor}相關發布的後續應用影響，"
        f"原文已提供具體文字依據：{LQ}{window}{RQ}，"
        f"決策者可依此原始資料評估具體場景的適用性，"
        f"並核查影響範圍，避免基於推測作出判斷。"
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
