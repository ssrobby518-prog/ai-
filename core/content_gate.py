"""Pre-LLM content gate utilities."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Any

DEFAULT_MIN_KEEP_ITEMS = 12
DEFAULT_MIN_KEEP_SIGNALS = 9

DEFAULT_GATE_LEVELS: tuple[tuple[int, int], ...] = (
    (1200, 3),
    (800, 2),
    (500, 2),
)

_SENTENCE_SPLIT_RE = re.compile(r"[.!?。？！]+")

REJECT_KEYWORDS = (
    "roundup",
    "digest",
    "index",
    "weekly",
    "top links",
    "subscribe",
    "sign in",
    "login",
)


@dataclass(frozen=True)
class AdaptiveGateStats:
    total: int
    kept: int
    level_used: int
    level_config: tuple[int, int]
    rejected_by_reason: dict[str, int]


def _normalize(text: str) -> str:
    return (text or "").strip()


def _count_sentences(text: str) -> int:
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text) if p.strip()]
    return len(parts)


def _hard_reject_reason(content: str) -> str | None:
    lowered = content.lower()
    for kw in REJECT_KEYWORDS:
        if kw in lowered:
            return f"rejected_keyword:{kw}"
    return None


def is_valid_article(
    text: str,
    *,
    content_min_len: int = 1200,
    min_sentences: int = 3,
) -> tuple[bool, str | None]:
    """Return whether text passes the content gate."""
    content = _normalize(text)

    hard_reason = _hard_reject_reason(content)
    if hard_reason:
        return False, hard_reason

    if len(content) < content_min_len:
        return False, "content_too_short"

    if _count_sentences(content) < min_sentences:
        return False, "insufficient_sentences"

    return True, None


def apply_adaptive_content_gate(
    items: list[Any],
    *,
    min_keep_items: int = DEFAULT_MIN_KEEP_ITEMS,
    levels: tuple[tuple[int, int], ...] = DEFAULT_GATE_LEVELS,
) -> tuple[list[Any], dict[int, str], AdaptiveGateStats]:
    """Apply adaptive gate levels after clean/dedup.

    Hard reject reasons always take priority and are never relaxed.
    """
    if not levels:
        raise ValueError("levels must not be empty")

    total = len(items)
    if total == 0:
        stats = AdaptiveGateStats(
            total=0,
            kept=0,
            level_used=1,
            level_config=levels[0],
            rejected_by_reason={},
        )
        return [], {}, stats

    hard_rejected: dict[int, str] = {}
    candidate_idx: list[int] = []
    for idx, item in enumerate(items):
        body = _normalize(getattr(item, "body", ""))
        hard_reason = _hard_reject_reason(body)
        if hard_reason:
            hard_rejected[idx] = hard_reason
        else:
            candidate_idx.append(idx)

    final_keep_idx: list[int] = []
    final_soft_rejected: dict[int, str] = {}
    level_used = 1
    level_config = levels[0]

    for lv_idx, (content_min_len, min_sentences) in enumerate(levels, start=1):
        keep_idx: list[int] = []
        soft_rejected: dict[int, str] = {}

        for idx in candidate_idx:
            ok, reason = is_valid_article(
                getattr(items[idx], "body", ""),
                content_min_len=content_min_len,
                min_sentences=min_sentences,
            )
            if ok:
                keep_idx.append(idx)
            else:
                soft_rejected[idx] = reason or "rejected_by_gate"

        final_keep_idx = keep_idx
        final_soft_rejected = soft_rejected
        level_used = lv_idx
        level_config = (content_min_len, min_sentences)

        if len(final_keep_idx) >= min_keep_items or lv_idx == len(levels):
            break

    rejected_map = dict(hard_rejected)
    rejected_map.update(final_soft_rejected)
    rejected_by_reason = dict(Counter(rejected_map.values()))

    kept_items = [items[idx] for idx in final_keep_idx]
    stats = AdaptiveGateStats(
        total=total,
        kept=len(kept_items),
        level_used=level_used,
        level_config=level_config,
        rejected_by_reason=rejected_by_reason,
    )
    return kept_items, rejected_map, stats
