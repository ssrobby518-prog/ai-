"""Exec Text Finalization — T1–T4 offline tests.

Tests confirm the three bug-fixes applied to actions/risks/narrative fields:
  (1) Fragment leak: fragment text is replaced, not passed through to output.
  (2) English-heavy skeleton: all-English text becomes 事件/影響 ZH skeleton.
  (3) Proof token whitelist (strict): generic numbers are NOT counted as proof.
  (4) Whitelisted proof tokens: version / B-M size / money / ISO date ARE counted.

All tests are pure-Python — no network, no PPT/DOCX, no pipeline.

T1  test_fragment_replaced_not_leaked
    — A typical ZH garbage fragment ("的趨勢，解決方 記") passed through
      is_placeholder_or_fragment returns True;
      when replaced by fallback + normalize_exec_text the result contains ZH
      substance and has leaked=0.

T2  test_english_heavy_gets_zh_skeleton
    — All-English text (ASCII > 60 %, CJK < 12) produces a 事件/影響 skeleton
      with ≥ 4 CJK characters; original entity token is still present.

T3  test_generic_numbers_not_proof_tokens
    — Bare percentages ("89%"), year numbers ("2025"), and generic unit
      numbers ("10 K") return 0 from count_proof_evidence_tokens.

T4  test_whitelisted_tokens_counted_as_proof
    — Version strings (v3.5.0), B/M size tokens (70B), dollar amounts ($6.6B),
      and ISO dates (2026-02-20) each return ≥ 1 from count_proof_evidence_tokens.
"""
from __future__ import annotations

import pytest

from utils.semantic_quality import is_placeholder_or_fragment
from utils.hybrid_glossing import normalize_exec_text, load_glossary
from utils.narrative_compact import count_proof_evidence_tokens


# ---------------------------------------------------------------------------
# Shared minimal glossary (avoids touching real JSON file)
# ---------------------------------------------------------------------------
_GLOSSARY = {
    "Transformer": "轉換器架構",
    "SWE-bench": "軟體工程基準測試",
}


# ---------------------------------------------------------------------------
# T1 — Fragment is replaced, not leaked
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fragment", [
    "的趨勢，解決方 記",   # template remnant: 解決方\s*記
    "解決方 記",            # template remnant direct
    "的AI應用",             # starts with ZH particle 的
    "記",                   # too short (< 8 non-space chars), no entity
])
def test_fragment_replaced_not_leaked(fragment: str) -> None:
    """T1: is_placeholder_or_fragment identifies the fragment; after replacement
    with a safe fallback and normalize_exec_text, the result carries real ZH
    content and the fragment itself does not appear in the output.
    """
    assert is_placeholder_or_fragment(fragment), (
        f"Expected {fragment!r} to be detected as fragment/placeholder"
    )

    # Simulate the guard applied in generators:
    safe_fallback = "持續監控此事件後續影響（T+7）。"
    if is_placeholder_or_fragment(fragment):
        result = normalize_exec_text(safe_fallback, _GLOSSARY, set())
    else:
        result = normalize_exec_text(fragment, _GLOSSARY, set())

    # The raw fragment must NOT appear verbatim in the output
    assert fragment not in result, (
        f"Fragment {fragment!r} leaked into output: {result!r}"
    )
    # The output must contain real ZH content
    zh_count = sum(1 for c in result if "\u4e00" <= c <= "\u9fff")
    assert zh_count >= 2, (
        f"Expected ZH content in replacement, got {zh_count} CJK chars: {result!r}"
    )


# ---------------------------------------------------------------------------
# T2 — English-heavy text gets ZH skeleton (事件/影響)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected_entity_hint", [
    (
        "OpenAI released GPT-4o with multimodal capabilities targeting enterprise.",
        "OpenAI",
    ),
    (
        "Anthropic raised $7.3B in Series E funding from Amazon and Google.",
        "Anthropic",
    ),
    (
        "NVIDIA launched the Blackwell B200 GPU with 208B transistors.",
        "NVIDIA",
    ),
])
def test_english_heavy_gets_zh_skeleton(text: str, expected_entity_hint: str) -> None:
    """T2: All-English text (ASCII > 60 %, ZH < 12 chars) receives a ZH skeleton
    containing 事件：/影響：; original entity token is still present in the output.
    """
    result = normalize_exec_text(text, _GLOSSARY, set())

    zh_count = sum(1 for c in result if "\u4e00" <= c <= "\u9fff")
    assert zh_count >= 4, (
        f"Expected >= 4 CJK chars in skeleton, got {zh_count}. Result: {result!r}"
    )
    assert "事件：" in result, f"Expected '事件：' marker in result: {result!r}"
    assert "影響：" in result, f"Expected '影響：' marker in result: {result!r}"
    assert expected_entity_hint in result, (
        f"Entity {expected_entity_hint!r} should still be present: {result!r}"
    )


# ---------------------------------------------------------------------------
# T3 — Generic numbers are NOT counted as strict proof tokens
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "The model achieved 89% accuracy on the test set.",
    "2025 was a significant year for AI development.",
    "We processed 10 K requests per second.",
    "The improvement was around 5 percent overall.",
    "Latency dropped by 30 percent compared to baseline.",
])
def test_generic_numbers_not_proof_tokens(text: str) -> None:
    """T3: Bare percentages, year numbers, and K-unit numbers score 0
    under the strict count_proof_evidence_tokens whitelist.
    """
    count = count_proof_evidence_tokens(text)
    assert count == 0, (
        f"Expected 0 strict proof tokens in {text!r}, got {count}"
    )


# ---------------------------------------------------------------------------
# T4 — Whitelisted tokens ARE counted as strict proof tokens
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,min_count", [
    ("Model version v3.5.0 is now available for download.", 1),   # version
    ("The 70B parameter model outperforms all prior baselines.", 1),   # B-size
    ("OpenAI raised $6.6B in its latest Series funding round.", 1),   # money
    ("The announcement was made on 2026-02-20.", 1),               # ISO date
    ("MMLU 91.5 and SWE-bench 72 with v2.1.0 weights.", 3),        # multiple
])
def test_whitelisted_tokens_counted_as_proof(text: str, min_count: int) -> None:
    """T4: Version strings, B/M param sizes, money amounts, and ISO dates
    are counted by the strict count_proof_evidence_tokens whitelist.
    """
    count = count_proof_evidence_tokens(text)
    assert count >= min_count, (
        f"Expected >= {min_count} strict proof token(s) in {text!r}, got {count}"
    )
