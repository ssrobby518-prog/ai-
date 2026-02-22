"""utils/text_final_sanitizer.py — 全鏈路名稱正規化 + 省略號清除（stdlib only）。

提供：
    normalize_names_zh(text: str) -> str
    strip_ellipsis(text: str) -> str
    final_sanitize(text: str) -> str
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 省略號清除
# ---------------------------------------------------------------------------

_ELLIPSIS_RE = re.compile(r"\u2026|\.{3,}")


def strip_ellipsis(text: str) -> str:
    """移除 '...' / '…' / U+2026（直接刪，不用替代符）。"""
    if not text:
        return text
    return _ELLIPSIS_RE.sub("", text)


# ---------------------------------------------------------------------------
# Claude 名稱正規化
# ---------------------------------------------------------------------------

_CORRECT_FORM = "Claude（Anthropic）"

# 雙層括號：Claude（克勞德（Anthropic...））
_DOUBLE_BRACKET_RE = re.compile(
    r"Claude\s*[（(]\s*(?:克勞德|克劳德)\s*[（(]\s*Anthropic[^）)]*[）)]\s*[）)]",
    re.IGNORECASE,
)

# Claude + 中文音譯（合體，含括號）
_CLAUDE_ZH_AFTER_RE = re.compile(
    r"Claude\s*（?\s*(?:克勞德|克劳德)\s*）?",
    re.IGNORECASE,
)

# 獨立中文音譯
_ZH_ALONE_RE = re.compile(r"克勞德|克劳德")

# Claude（Anthropic AI） 或 Claude(Anthropic AI)
_CLAUDE_ANTHROPIC_AI_RE = re.compile(
    r"Claude\s*[（(]\s*Anthropic\s*AI\s*[）)]",
    re.IGNORECASE,
)

# Claude（Anthropic）AI → 多餘 AI 尾綴
_CLAUDE_CORRECT_AI_SUFFIX_RE = re.compile(
    r"Claude\s*（\s*Anthropic\s*）\s*AI",
    re.IGNORECASE,
)

# Claude (Anthropic) 半形括號
_CLAUDE_HALF_BRACKET_RE = re.compile(
    r"Claude\s*\(\s*Anthropic\s*\)",
    re.IGNORECASE,
)

# Claude（ Anthropic ）多餘空格（全形）
_CLAUDE_FULLWIDTH_SPACE_RE = re.compile(
    r"Claude\s*（\s*Anthropic\s*）",
    re.IGNORECASE,
)


def normalize_names_zh(text: str) -> str:
    """統一 Claude 名稱寫法為 Claude（Anthropic）。

    規則：
    - 克勞德/克劳德 → Claude（Anthropic）
    - Claude (Anthropic) / Claude(Anthropic) → Claude（Anthropic）
    - Claude（Anthropic AI）/ Claude（Anthropic）AI → Claude（Anthropic）
    - 雙層括號變體 → Claude（Anthropic）
    """
    if not text:
        return text
    # 1. 雙層括號複雜變體（優先處理）
    text = _DOUBLE_BRACKET_RE.sub(_CORRECT_FORM, text)
    # 2. Claude + 中文音譯合體
    text = _CLAUDE_ZH_AFTER_RE.sub(_CORRECT_FORM, text)
    # 3. 獨立中文音譯
    text = _ZH_ALONE_RE.sub(_CORRECT_FORM, text)
    # 4. Claude（Anthropic AI）
    text = _CLAUDE_ANTHROPIC_AI_RE.sub(_CORRECT_FORM, text)
    # 5. Claude（Anthropic）AI 多餘尾綴
    text = _CLAUDE_CORRECT_AI_SUFFIX_RE.sub(_CORRECT_FORM, text)
    # 6. Claude (Anthropic) 半形
    text = _CLAUDE_HALF_BRACKET_RE.sub(_CORRECT_FORM, text)
    # 7. Claude（ Anthropic ）多餘空格（全形）
    text = _CLAUDE_FULLWIDTH_SPACE_RE.sub(_CORRECT_FORM, text)
    return text


# ---------------------------------------------------------------------------
# 最終清洗
# ---------------------------------------------------------------------------

def final_sanitize(text: str) -> str:
    """依序：strip_ellipsis → normalize_names_zh → 去多餘空白。"""
    if not text:
        return text
    text = strip_ellipsis(text)
    text = normalize_names_zh(text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()
