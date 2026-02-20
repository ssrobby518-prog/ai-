"""Offline tests for EN-ZH Hybrid Glossing v1.

All tests are pure-Python — no network, no pipeline, no PPT/DOCX.

Rule change (v1.1): Big Tech / AI Lab names (OpenAI, NVIDIA, Microsoft,
Google, Anthropic, AWS, Meta, Apple, Intel, xAI) are NEVER annotated,
even when present in the glossary.  Other proper nouns (benchmarks, tools,
frameworks, non-big-tech entities) still receive first-occurrence annotations.

T1  test_proper_noun_gloss_added_first_occurrence
    — first occurrence of a non-big-tech glossary term (Transformer) gains
      ZH annotation; second call with the same `seen` set does NOT re-annotate.

T2  test_not_all_english_enforced
    — all-English text (ASCII > 60 %, ZH < 12 chars) receives a ZH skeleton.

T3  test_non_big_tech_proper_noun_kept_english_with_zh_explain
    — non-big-tech proper noun (SWE-bench) keeps its English form with ZH
      parenthetical; big-tech company names do NOT receive annotation.

T4  test_does_not_touch_version_money_params_tokens
    — version strings, dollar amounts, percentages pass through unchanged.

T5  test_no_fragment_leak_after_normalization
    — empty / whitespace / very-short fragment text is returned as-is
      (no ZH skeleton is fabricated from near-empty input).

T6  test_big_ai_company_no_gloss
    — Big Tech / AI Lab names must NOT receive ZH annotation.

T7  test_non_big_proper_noun_still_glossed
    — Benchmark / tool names in glossary (e.g. SWE-bench) still get ZH annotation.
"""
from __future__ import annotations

import pytest

from utils.hybrid_glossing import (
    NO_GLOSS_TERMS,
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
    "OpenAI": "開放人工智慧",        # Big Tech → will be skipped by NO_GLOSS_TERMS
    "Anthropic": "人工智慧安全公司",  # Big Tech → will be skipped by NO_GLOSS_TERMS
    "NVIDIA": "輝達（繪圖晶片巨頭）",  # Big Tech → will be skipped by NO_GLOSS_TERMS
    "Transformer": "轉換器架構",       # Non-big-tech → still glossed
    "Google DeepMind": "谷歌深度心智",  # Compound name, NOT in NO_GLOSS_TERMS → still glossed
    "SWE-bench": "軟體工程基準測試",    # Benchmark → still glossed
}


# ---------------------------------------------------------------------------
# T1
# ---------------------------------------------------------------------------


def test_proper_noun_gloss_added_first_occurrence():
    """T1: First occurrence of a non-big-tech glossary term gains ZH annotation;
    second call with the same `seen` set does NOT re-annotate.

    Uses 'Transformer' (architecture / framework — not a Big Tech company).
    """
    seen: set = set()

    # First occurrence — must add annotation
    out1 = apply_glossary("The Transformer architecture powers modern LLMs.", _GLOSSARY, seen)
    assert "Transformer（轉換器架構）" in out1, (
        f"Expected ZH annotation on first occurrence, got: {out1!r}"
    )

    # Second call with SAME seen set — must NOT re-annotate
    out2 = apply_glossary("Transformer models continue to improve rapidly.", _GLOSSARY, seen)
    assert "Transformer（轉換器架構）" not in out2, (
        f"Should NOT re-annotate on second call with same seen set: {out2!r}"
    )
    # But the English term itself must still appear
    assert "Transformer" in out2, f"Term should still be present (without annotation): {out2!r}"


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


def test_non_big_tech_proper_noun_kept_english_with_zh_explain():
    """T3: Non-big-tech proper noun (benchmark / tool) keeps its English form;
    ZH parenthetical is appended on first occurrence.

    Big Tech / AI Lab companies (OpenAI, Anthropic, NVIDIA, …) are explicitly
    excluded by NO_GLOSS_TERMS and must NOT receive any annotation.
    """
    seen: set = set()
    out = apply_glossary("Model scores 72.1% on SWE-bench for coding tasks.", _GLOSSARY, seen)

    # English term preserved verbatim
    assert "SWE-bench" in out, f"English term missing: {out!r}"
    # ZH annotation present
    assert "軟體工程基準測試" in out, f"ZH annotation missing: {out!r}"
    # Must be in the 「term（zh）」 form
    assert "SWE-bench（軟體工程基準測試）" in out, (
        f"Expected 'SWE-bench（軟體工程基準測試）' in output: {out!r}"
    )
    # Big Tech company in same text must NOT get annotated
    out_mixed = apply_glossary(
        "OpenAI's model scores 72.1% on SWE-bench.", _GLOSSARY, set()
    )
    assert "OpenAI（" not in out_mixed, f"OpenAI must not be annotated: {out_mixed!r}"
    assert "SWE-bench（軟體工程基準測試）" in out_mixed, (
        f"SWE-bench must still be annotated: {out_mixed!r}"
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


# ---------------------------------------------------------------------------
# T6
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("company", [
    "OpenAI",
    "NVIDIA",
    "Microsoft",
    "Google",
    "Anthropic",
    "AWS",
    "Meta",
    "Apple",
    "Intel",
    "xAI",
])
def test_big_ai_company_no_gloss(company: str):
    """T6: Every Big Tech / AI Lab name in NO_GLOSS_TERMS must NOT receive a ZH annotation,
    even when a glossary entry exists for that name.
    """
    # Build a glossary that explicitly contains the company (worst-case scenario)
    glossary_with_company = dict(_GLOSSARY)
    glossary_with_company[company] = "某大公司（測試）"

    text = f"{company} released a major update to its AI platform."
    result = apply_glossary(text, glossary_with_company, set())

    # Must NOT have the annotation bracket after the company name
    assert f"{company}（" not in result, (
        f"{company!r} must NOT receive ZH annotation. Got: {result!r}"
    )
    # But the company name itself must still appear unchanged
    assert company in result, (
        f"{company!r} must still appear in text. Got: {result!r}"
    )
    # Also verify the constant contains this company
    assert company in NO_GLOSS_TERMS, f"{company!r} must be in NO_GLOSS_TERMS"


# ---------------------------------------------------------------------------
# T7
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("term,zh", [
    ("SWE-bench", "軟體工程基準測試"),
    ("Transformer", "轉換器架構"),
    ("Google DeepMind", "谷歌深度心智"),
])
def test_non_big_proper_noun_still_glossed(term: str, zh: str):
    """T7: Non-big-tech proper nouns (benchmarks, tools, compound lab names) in the
    glossary still receive first-occurrence ZH annotation.
    """
    result = apply_glossary(f"The {term} is widely used in AI research.", _GLOSSARY, set())
    assert f"{term}（{zh}）" in result, (
        f"Expected '{term}（{zh}）' in output, got: {result!r}"
    )
