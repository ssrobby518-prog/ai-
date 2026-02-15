"""Pre-LLM content gate for article validity checks."""

from __future__ import annotations

import re

_CONTENT_MIN_LEN = 1200
_MIN_SENTENCES = 3
_SENTENCE_SPLIT_RE = re.compile(r"[.!?。？！]+")

_REJECT_KEYWORDS = (
    "roundup",
    "digest",
    "index",
    "weekly",
    "top links",
    "subscribe",
    "sign in",
    "login",
)


def _count_sentences(text: str) -> int:
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text) if p.strip()]
    return len(parts)


def is_valid_article(text: str) -> tuple[bool, str | None]:
    """Return whether text passes pre-LLM quality gates.

    Returns:
        (True, None) when valid.
        (False, reason) when rejected.
    """
    content = (text or "").strip()

    if len(content) < _CONTENT_MIN_LEN:
        return False, "content_too_short"

    if _count_sentences(content) < _MIN_SENTENCES:
        return False, "insufficient_sentences"

    lowered = content.lower()
    for kw in _REJECT_KEYWORDS:
        if kw in lowered:
            return False, f"rejected_keyword:{kw}"

    return True, None

