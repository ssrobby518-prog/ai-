"""Offline tests for EN-ZH Hybrid Glossing v1.

All five tests are pure-Python — no network, no pipeline, no PPT/DOCX.

T1  test_proper_noun_gloss_added_first_occurrence
    — first occurrence of a glossary term gains ZH annotation;
      second call with the same `seen` set does NOT re-annotate.

T2  test_not_all_english_enforced
    — all-English text (ASCII > 60 %, ZH < 12 chars) receives a ZH skeleton.

T3  test_company_name_kept_english_with_zh_explain
    — company name stays as-is in English; only a ZH parenthetical is added.

T4  test_does_not_touch_version_money_params_tokens
    — version strings, dollar amounts, percentages pass through unchanged.

T5  test_no_fragment_leak_after_normalization
    — empty / whitespace / very-short fragment text is returned as-is
      (no ZH skeleton is fabricated from near-empty input).
"""
from __future__ import annotations

import pytest

from utils.hybrid_glossing import (
    apply_glossary,
    ensure_not_all_english,
    extract_proper_nouns,
    get_gloss_stats,
    normalize_exec_text,
    reset_gloss_stats,
)

# ---------------------------------------------------------------------------
# Minimal inline glossary for all tests — avoids touching the real JSON file
# ---------------------------------------------------------------------------

_GLOSSARY = {
    "OpenAI": "開放人工智慧",
    "Anthropic": "人工智慧安全公司",
    "NVIDIA": "輝達（繪圖晶片巨頭）",
    "Transformer": "轉換器架構",
    "Google DeepMind": "谷歌深度心智",
}


# ---------------------------------------------------------------------------
# T1
# ---------------------------------------------------------------------------


def test_proper_noun_gloss_added_first_occurrence():
    """T1: First occurrence of a glossary term gains ZH annotation;
    second call with the same `seen` set does NOT re-annotate.
    """
    seen: set = set()

    # First occurrence — must add annotation
    out1 = apply_glossary("OpenAI released a new model.", _GLOSSARY, seen)
    assert "OpenAI（開放人工智慧）" in out1, (
        f"Expected ZH annotation on first occurrence, got: {out1!r}"
    )

    # Second call with SAME seen set — must NOT re-annotate
    out2 = apply_glossary("OpenAI continues to grow.", _GLOSSARY, seen)
    assert "OpenAI（開放人工智慧）" not in out2, (
        f"Should NOT re-annotate on second call with same seen set: {out2!r}"
    )
    # But the English term itself must still appear
    assert "OpenAI" in out2, f"Term should still be present (without annotation): {out2!r}"


# ---------------------------------------------------------------------------
# T2
# ---------------------------------------------------------------------------


def test_not_all_english_enforced():
    """T2: All-English paragraph (ASCII > 60 %, ZH < 12 chars) gets a ZH skeleton."""
    text = (
        "OpenAI released GPT-4o with improved reasoning and multimodal capabilities, "
        "targeting enterprise AI workflows."
    )
    result = ensure_not_all_english(text)

    # Result must now contain CJK characters
    zh_count = sum(1 for c in result if "\u4e00" <= c <= "\u9fff")
    assert zh_count >= 2, (
        f"Expected >= 2 CJK chars after ensure_not_all_english, got {zh_count}. "
        f"Result: {result!r}"
    )
    # Original text must be preserved inside the result
    assert "OpenAI" in result, "Original entity should still be present"


# ---------------------------------------------------------------------------
# T3
# ---------------------------------------------------------------------------


def test_company_name_kept_english_with_zh_explain():
    """T3: Glossary term keeps its English form; ZH parenthetical is appended."""
    seen: set = set()
    out = apply_glossary("Anthropic launched Claude 3.", _GLOSSARY, seen)

    # English term preserved verbatim
    assert "Anthropic" in out, f"English term missing: {out!r}"
    # ZH annotation present
    assert "人工智慧安全公司" in out, f"ZH annotation missing: {out!r}"
    # Rest of text preserved
    assert "Claude 3" in out, f"Rest of text should be preserved: {out!r}"
    # Must be in the 「term（zh）」 form, not the other way around
    assert "Anthropic（人工智慧安全公司）" in out, (
        f"Expected 'Anthropic（人工智慧安全公司）' in output: {out!r}"
    )


# ---------------------------------------------------------------------------
# T4
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", [
    "v1.2.3",
    "$500M",
    "89.5%",
    "1024",
    "T+7",
])
def test_does_not_touch_version_money_params_tokens(token: str):
    """T4: Version strings, dollar amounts, percentages, numbers pass through unchanged."""
    text = f"Model achieves {token} on the benchmark."
    result = normalize_exec_text(text, _GLOSSARY, set())
    assert token in result, (
        f"Token {token!r} was garbled or removed. Output: {result!r}"
    )


# ---------------------------------------------------------------------------
# T5
# ---------------------------------------------------------------------------


def test_no_fragment_leak_after_normalization():
    """T5: Empty / whitespace / very-short fragment text is NOT wrapped in a ZH skeleton.

    ensure_not_all_english has a minimum-length guard (< 15 chars → pass-through)
    that prevents fragment content from being promoted into a fabricated sentence.
    """
    # Empty string → returns empty string
    assert normalize_exec_text("", _GLOSSARY) == "", (
        "Empty string must return empty string"
    )

    # Whitespace-only → returns as-is (stripped is empty)
    ws_result = normalize_exec_text("   ", _GLOSSARY)
    assert ws_result.strip() == "", (
        f"Whitespace-only input should remain whitespace: {ws_result!r}"
    )

    # Pure fragment (< 15 chars, all ASCII) — must NOT gain a ZH skeleton
    for fragment in ("2.", "Last July", "WHY IT"):
        result = normalize_exec_text(fragment, _GLOSSARY)
        zh_count = sum(1 for c in result if "\u4e00" <= c <= "\u9fff")
        assert zh_count == 0, (
            f"Fragment {fragment!r} should not gain CJK chars. Got: {result!r}"
        )
