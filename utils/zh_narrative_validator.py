"""Deterministic validator for ZH narrative card fields.

Centralises the machine-checkable format rules that mirror the
EXEC_ZH_NARRATIVE_WITH_QUOTE_HARD gate so that failures can be caught
at build-time (and optionally self-healed) rather than only at post-run
gate evaluation.

Usage
-----
    from utils.zh_narrative_validator import validate_zh_card_fields

    ok, reasons = validate_zh_card_fields(
        q1_zh, q2_zh,
        quote_window_1, quote_window_2,
        quote_1, quote_2,
    )
    if not ok:
        log.warning("ZH narrative validation failed: %s", reasons)

Format specification (ZH_NARRATIVE_SPEC)
-----------------------------------------
All rules are machine-checkable; prose / narrative coherence is NOT
checked here (that belongs in the prompt only).

Per q_zh field:
  - ZH char count  : >= ZH_NARRATIVE_MIN_ZH_CHARS (40) CJK characters
  - EN ratio       : <= ZH_NARRATIVE_MAX_EN_RATIO  (0.50) Latin letters
  - Window embed   : fullwidth「quote_window」 must appear verbatim
  - Style ban      : no boilerplate echoing prohibited template phrases
  - Naming ban     : no Chinese transliterations of "Claude"

Per quote_window:
  - Non-empty      : len > 0
  - Substring      : must be a verbatim substring of the raw English quote

Note on quote_window normalisation
-----------------------------------
quote_window_1 / quote_window_2 are stored in the final_cards dict in their
ORIGINAL form (before any transliteration normalisation).  The q_zh templates
must embed them WITHOUT applying _normalize_claude_name to the window text,
so that the gate check  「quote_window」 ∈ q_zh  succeeds.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Format specification constants
# ---------------------------------------------------------------------------

#: Minimum number of CJK characters required in each q_zh field.
ZH_NARRATIVE_MIN_ZH_CHARS: int = 40

#: Maximum ratio of Latin letters to total characters in each q_zh field.
ZH_NARRATIVE_MAX_EN_RATIO: float = 0.50

#: Minimum character length for a quote_window value.
ZH_NARRATIVE_MIN_QW_LEN: int = 10

#: Fullwidth left/right quotation marks used to embed quote windows.
FULLWIDTH_LEFT_BRACKET: str = "\u300c"   # 「
FULLWIDTH_RIGHT_BRACKET: str = "\u300d"  # 」

# ---------------------------------------------------------------------------
# Internal regular expressions (mirrors run_once.py constants)
# ---------------------------------------------------------------------------

_ZH_RE = re.compile(r"[\u4e00-\u9fff]")
_EN_RE = re.compile(r"[a-zA-Z]")

_STYLE_SANITY_RE = re.compile(
    r"\u5f15\u767c.*(?:\u8a0e\u8ad6|\u95dc\u6ce8|\u71b1\u8b70)"
    r"|\u5177\u6709.*(?:\u5be6\u8cea|\u91cd\u5927).*(?:\u5f71\u97ff|\u610f\u7fa9)"
    r"|(?:\u5404\u65b9|\u696d\u754c).*(?:\u8457\u624b|\u6b63).*(?:\u8a55\u4f30|\u8ffd\u8e64).*(?:\u5f8c\u7e8c|\u5f71\u97ff|\u52d5\u5411)"
    r"|\u6599\u5c07\u5f71\u97ff.*(?:\u683c\u5c40|\u8d70\u5411|\u5e02\u5834)"
    r"|\u6700\u65b0\u516c\u544a\u984c\u793a"            # 最新公告顯示 — old Q1_zh template phrase
    r"|\u78ba\u8a8d.*\u539f\u6587\u51fa\u8655"          # 確認.*原文出處
    r"|\u539f\u6587\u5df2\u63d0\u4f9b.*\u4f9d\u64da"   # 原文已提供.*依據 — old Q2_zh template phrase
    r"|\u907f\u514d\u57fa\u65bc\u63a8\u6e2c",           # 避免基於推測
    re.IGNORECASE,
)

# Chinese transliterations of "Claude" that must not appear in q_zh.
_CLAUDE_TRANSLIT_RE = re.compile(
    r"(?:\u514b\u52de\u5fb7|\u514b\u52b3\u5fb7|\u67ef\u52de\u5fb7"
    r"|\u53ef\u52de\u5fb7|\u53ef\u52b3\u5fb7|\u514b\u6d1b\u5fb7)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public validator
# ---------------------------------------------------------------------------

def validate_zh_card_fields(
    q1_zh: str,
    q2_zh: str,
    quote_window_1: str,
    quote_window_2: str,
    quote_1: str,
    quote_2: str,
) -> tuple[bool, list[str]]:
    """Validate ZH narrative fields against EXEC_ZH_NARRATIVE_WITH_QUOTE_HARD.

    Parameters
    ----------
    q1_zh, q2_zh:
        The Chinese narrative texts generated for Q1 / Q2.
    quote_window_1, quote_window_2:
        Short verbatim English fragments (20-30 chars) extracted from
        quote_1 / quote_2.  These are stored *as-is* (not transliteration-
        normalised) and must appear inside 「 」 in the corresponding q_zh.
    quote_1, quote_2:
        The raw verbatim English source quotes (up to 110 chars).

    Returns
    -------
    (ok, reasons)
        ok      – True when every check passes.
        reasons – List of failing criterion names (empty when ok=True).
    """
    lq = FULLWIDTH_LEFT_BRACKET
    rq = FULLWIDTH_RIGHT_BRACKET
    reasons: list[str] = []

    # --- quote_window non-empty --------------------------------------------
    if not quote_window_1:
        reasons.append("QW1_EMPTY")
    if not quote_window_2:
        reasons.append("QW2_EMPTY")

    # --- 「quote_window」 embedded verbatim in q_zh -------------------------
    if quote_window_1 and (lq + quote_window_1 + rq) not in q1_zh:
        reasons.append("Q1_ZH_NO_WINDOW")
    if quote_window_2 and (lq + quote_window_2 + rq) not in q2_zh:
        reasons.append("Q2_ZH_NO_WINDOW")

    # --- CJK character count -----------------------------------------------
    if len(_ZH_RE.findall(q1_zh)) < ZH_NARRATIVE_MIN_ZH_CHARS:
        reasons.append("Q1_ZH_CHARS_LOW")
    if len(_ZH_RE.findall(q2_zh)) < ZH_NARRATIVE_MIN_ZH_CHARS:
        reasons.append("Q2_ZH_CHARS_LOW")

    # --- English ratio ≤ 50% -----------------------------------------------
    if q1_zh and len(_EN_RE.findall(q1_zh)) / len(q1_zh) > ZH_NARRATIVE_MAX_EN_RATIO:
        reasons.append("Q1_EN_RATIO_HIGH")
    if q2_zh and len(_EN_RE.findall(q2_zh)) / len(q2_zh) > ZH_NARRATIVE_MAX_EN_RATIO:
        reasons.append("Q2_EN_RATIO_HIGH")

    # --- quote_window is verbatim substring of raw quote -------------------
    if quote_window_1 and quote_window_1 not in quote_1:
        reasons.append("QW1_NOT_SUBSTRING")
    if quote_window_2 and quote_window_2 not in quote_2:
        reasons.append("QW2_NOT_SUBSTRING")

    # --- style ban (echo-template boilerplate) -----------------------------
    if _STYLE_SANITY_RE.search(q1_zh + " " + q2_zh):
        reasons.append("STYLE_SANITY")

    # --- naming ban (Claude transliterations) ------------------------------
    if _CLAUDE_TRANSLIT_RE.search(q1_zh + " " + q2_zh):
        reasons.append("NAMING")

    return (len(reasons) == 0, reasons)
