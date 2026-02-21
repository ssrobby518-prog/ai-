"""utils/exec_sanitizer.py — Final-mile sanitizer for executive output text.

Strips internal tags (WATCH:/TEST:/MOVE:/etc.) and banned template substrings
from any text going into PPT/DOCX slides before they reach the renderer.

Stdlib-only. No new pip deps.

Public API:
    strip_internal_tags(text: str) -> str
    is_banned(text: str) -> bool
    sanitize_exec_text(text: str) -> str
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Banned substrings — any text containing these must be replaced
# ---------------------------------------------------------------------------

BANNED_SUBSTRINGS: list[str] = [
    # Truncation artifact
    "的趨勢，解決方 記",
    # Generic placeholder — proof must always have real source + date
    "詳見原始來源",
    # Empty-bucket hollow content
    "監控中 本欄暫無事件",
    # v5.2.3 density-guard leakage (Q1 template)
    "Evidence summary: sources=",
    # v5.2.3 density-guard leakage (Q2 template)
    "Key terms: ",
    # v5.2.3 density-guard leakage (Q3 action templates)
    "validate source evidence and related numbers",
    "run small-scope checks against current workflow",
    "escalate only if next scan confirms sustained",
    # Generic filler phrases
    "現有策略與資源配置",
    "高關注度",
    "新穎性",
    # v3 synonymous empty-bucket hollow content
    "此欄暫無資料；持續掃描來源中",
    "本欄暫無資料",
]

# ---------------------------------------------------------------------------
# Internal tag prefix patterns — stripped from action items
# ---------------------------------------------------------------------------

INTERNAL_TAG_PREFIX_RE = re.compile(
    r"^(WATCH|TEST|MOVE|FIX|TODO|NOTE)\b\s*[：:]\s*",
    re.IGNORECASE,
)

# Safe fallback when a string is entirely banned
_SAFE_FALLBACK = "持續監控此事件後續發展（T+7）。"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def strip_internal_tags(text: str) -> str:
    """Remove WATCH:/TEST:/MOVE: prefix from action items (case-insensitive)."""
    if not text:
        return text
    return INTERNAL_TAG_PREFIX_RE.sub("", text.strip())


def is_banned(text: str) -> bool:
    """Return True if text contains any banned substring."""
    if not text:
        return False
    for b in BANNED_SUBSTRINGS:
        if b in text:
            return True
    return False


def sanitize_exec_text(text: str) -> str:
    """Full sanitization pipeline:

    1. Strip internal tag prefix (WATCH:/TEST:/MOVE:).
    2. If result contains any banned substring → return _SAFE_FALLBACK.
    3. Otherwise return cleaned text.
    """
    if not text:
        return text
    cleaned = strip_internal_tags(str(text))
    if is_banned(cleaned):
        return _SAFE_FALLBACK
    return cleaned
