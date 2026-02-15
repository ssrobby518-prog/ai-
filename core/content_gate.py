"""Pre-LLM content gate utilities."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Any

DEFAULT_MIN_KEEP_ITEMS = 12
DEFAULT_MIN_KEEP_SIGNALS = 9

DEFAULT_GATE_LEVELS: tuple[tuple[int, int], ...] = (
    (1200, 3),  # strict
    (600, 2),   # relaxed
)

_SENTENCE_SPLIT_RE = re.compile(r"[.!?。？！]+")
_SENTENCE_PUNCT_RE = re.compile(r"[.!?。？！]")

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

_FRAGMENT_TERMS = (
    "last july was",
    "this week was",
    "this month was",
)

_FRAGMENT_REGEXES = (
    re.compile(r"\b(?:was|is|are)\s*(?:\.\.\.|[,;:])\s*$", re.IGNORECASE),
    re.compile(r"\b(?:this|that|it)\s+\w+\s+(?:was|is|are)\s*$", re.IGNORECASE),
)


@dataclass(frozen=True)
class AdaptiveGateStats:
    total: int
    passed_strict: int
    passed_relaxed: int
    rejected_total: int
    rejected_by_reason: dict[str, int]
    rejected_reason_top: list[tuple[str, int]]
    level_used: int
    level_config: tuple[int, int]

    @property
    def kept(self) -> int:
        return self.passed_strict + self.passed_relaxed


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


def _is_fragment_placeholder(content: str) -> bool:
    stripped = _normalize(content)
    if not stripped:
        return True

    lowered = stripped.lower()

    if any(term in lowered for term in _FRAGMENT_TERMS):
        return True

    if stripped.endswith(","):
        return True

    if len(stripped) < 80 and _SENTENCE_PUNCT_RE.search(stripped) is None:
        return True

    if re.search(r"\b(?:was|is|are)\s*$", lowered):
        return True

    return any(rx.search(stripped) is not None for rx in _FRAGMENT_REGEXES)


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

    if _is_fragment_placeholder(content):
        return False, "fragment_placeholder"

    if len(content) < content_min_len:
        return False, "content_too_short"

    if _count_sentences(content) < min_sentences:
        return False, "insufficient_sentences"

    return True, None


def _set_rejected_reason(item: Any, reason: str) -> None:
    try:
        setattr(item, "rejected_reason", reason)
    except Exception:
        pass


def _clear_rejected_reason(item: Any) -> None:
    try:
        setattr(item, "rejected_reason", "")
    except Exception:
        pass


def apply_adaptive_content_gate(
    items: list[Any],
    *,
    min_keep_items: int = DEFAULT_MIN_KEEP_ITEMS,
    levels: tuple[tuple[int, int], ...] = DEFAULT_GATE_LEVELS,
) -> tuple[list[Any], dict[int, str], AdaptiveGateStats]:
    """Apply adaptive gate levels after clean/dedup.

    Hard reject rules remain strict in every level. Relaxation only lowers
    content length/sentence thresholds.
    """
    if len(levels) < 1:
        raise ValueError("levels must contain at least one gate config")

    strict_level = levels[0]
    relaxed_level = levels[1] if len(levels) > 1 else levels[0]

    total = len(items)
    if total == 0:
        stats = AdaptiveGateStats(
            total=0,
            passed_strict=0,
            passed_relaxed=0,
            rejected_total=0,
            rejected_by_reason={},
            rejected_reason_top=[],
            level_used=1,
            level_config=strict_level,
        )
        return [], {}, stats

    hard_rejected: dict[int, str] = {}
    candidates: list[int] = []
    for idx, item in enumerate(items):
        body = _normalize(getattr(item, "body", ""))
        hard_reason = _hard_reject_reason(body)
        if hard_reason:
            hard_rejected[idx] = hard_reason
            _set_rejected_reason(item, hard_reason)
        else:
            candidates.append(idx)

    strict_pass_idx: list[int] = []
    strict_reject_idx: list[int] = []
    strict_rejected_reasons: dict[int, str] = {}
    strict_min_len, strict_min_sentences = strict_level

    for idx in candidates:
        item = items[idx]
        ok, reason = is_valid_article(
            getattr(item, "body", ""),
            content_min_len=strict_min_len,
            min_sentences=strict_min_sentences,
        )
        if ok:
            strict_pass_idx.append(idx)
            _clear_rejected_reason(item)
        else:
            strict_reject_idx.append(idx)
            strict_rejected_reasons[idx] = reason or "rejected_by_gate"

    relaxed_pass_idx: list[int] = []
    final_soft_rejected: dict[int, str]
    level_used = 1
    level_config = strict_level

    if len(strict_pass_idx) >= min_keep_items or strict_level == relaxed_level:
        final_soft_rejected = strict_rejected_reasons
    else:
        level_used = 2
        level_config = relaxed_level
        relaxed_min_len, relaxed_min_sentences = relaxed_level
        relaxed_rejected: dict[int, str] = {}

        for idx in strict_reject_idx:
            item = items[idx]
            ok, reason = is_valid_article(
                getattr(item, "body", ""),
                content_min_len=relaxed_min_len,
                min_sentences=relaxed_min_sentences,
            )
            if ok:
                relaxed_pass_idx.append(idx)
                _clear_rejected_reason(item)
            else:
                relaxed_reason = reason or "rejected_by_gate"
                relaxed_rejected[idx] = relaxed_reason
                _set_rejected_reason(item, relaxed_reason)

        final_soft_rejected = relaxed_rejected

    kept_idx = strict_pass_idx + relaxed_pass_idx
    kept_items = [items[idx] for idx in kept_idx]

    rejected_map = dict(hard_rejected)
    rejected_map.update(final_soft_rejected)

    for idx in kept_idx:
        _clear_rejected_reason(items[idx])

    for idx, reason in rejected_map.items():
        _set_rejected_reason(items[idx], reason)

    rejected_by_reason = dict(Counter(rejected_map.values()))
    rejected_reason_top = sorted(
        rejected_by_reason.items(),
        key=lambda kv: kv[1],
        reverse=True,
    )[:5]

    stats = AdaptiveGateStats(
        total=total,
        passed_strict=len(strict_pass_idx),
        passed_relaxed=len(relaxed_pass_idx),
        rejected_total=len(rejected_map),
        rejected_by_reason=rejected_by_reason,
        rejected_reason_top=rejected_reason_top,
        level_used=level_used,
        level_config=level_config,
    )
    return kept_items, rejected_map, stats