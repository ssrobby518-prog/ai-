"""Text quality utilities — fragment detection and trailing-fragment trimming.

Used by content_strategy to sanitize all text bound for PPT/DOCX output.
"""

from __future__ import annotations

import re

# High-risk trailing words that indicate a sentence was cut mid-thought.
_TRAILING_ZH = ("的", "了", "而", "與", "來", "記", "是", "在", "和", "或", "及", "對", "從", "向", "把", "被", "讓", "給")
_TRAILING_EN_RE = re.compile(
    r"\b(?:to|and|or|by|the|a|an|of|in|on|at|for|with|is|was|are|were|that|this|it)\s*$",
    re.IGNORECASE,
)

# Sentence boundary characters.
_SENTENCE_END_RE = re.compile(r"[.!?。！？;；]")

# Evidence patterns: numbers, URLs, proper nouns.
_HAS_NUMBER_RE = re.compile(r"\d")
_HAS_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_HAS_ENTITY_RE = re.compile(r"[A-Z][a-z]{2,}|[\u4e00-\u9fff]{2,}")


def trim_trailing_fragment(text: str) -> str:
    """If the text ends on a high-risk trailing word, trim back to the previous sentence boundary."""
    if not text or not text.strip():
        return ""
    s = text.strip()

    # Check Chinese trailing particles.
    if s and s[-1] in _TRAILING_ZH:
        # Find previous sentence boundary.
        m = list(_SENTENCE_END_RE.finditer(s[:-1]))
        if m:
            return s[: m[-1].end()].strip()
        # No sentence boundary found — return as-is rather than destroying everything.
        return s

    # Check English trailing words.
    if _TRAILING_EN_RE.search(s):
        m = list(_SENTENCE_END_RE.finditer(s))
        if m:
            return s[: m[-1].end()].strip()
        return s

    return s


def is_fragment(text: str) -> bool:
    """Return True if text is an isolated short fragment without substance."""
    if not text:
        return True
    s = text.strip()
    if len(s) < 12:
        if _HAS_NUMBER_RE.search(s):
            return False
        if _HAS_URL_RE.search(s):
            return False
        if _HAS_ENTITY_RE.search(s):
            return False
        return True
    return False
