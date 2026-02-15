"""Pre-LLM content gate utilities."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
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

_DENSITY_NUMERIC_RE = re.compile(
    r"(?:\b20\d{2}\b)|(?:\bv?\d+(?:\.\d+)?(?:\.\d+)?(?:%|x|ms|gb|mb|k|m|b)?\b)|(?:[$¥€]\s*\d+)",
    re.IGNORECASE,
)
_DENSITY_ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z0-9\-_]{2,}\b")
_DENSITY_MODEL_HINTS = (
    "gpt",
    "llama",
    "qwen",
    "deepseek",
    "cuda",
    "h100",
    "nvidia",
    "openai",
    "claude",
    "gemini",
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
    soft_density_pass: int = 0
    density_score_top5: list[tuple[str, str, int]] = field(default_factory=list)  # (title, url, score)

    @property
    def kept(self) -> int:
        return self.passed_strict + self.passed_relaxed + self.soft_density_pass

    @property
    def soft_pass_total(self) -> int:
        # Soft pass includes relaxed threshold + density fallback.
        return self.passed_relaxed + self.soft_density_pass

    @property
    def hard_pass_total(self) -> int:
        return self.passed_strict


def _normalize(text: str) -> str:
    return (text or "").strip()


def _count_sentences(text: str) -> int:
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text) if p.strip()]
    return len(parts)


def density_score(text: str) -> int:
    """Score content density (0~100) for soft-pass fallback routing."""
    content = _normalize(text)
    if not content:
        return 0

    lowered = content.lower()
    numeric_hits = len(_DENSITY_NUMERIC_RE.findall(content))
    entity_hits = len(_DENSITY_ENTITY_RE.findall(content))
    model_hint_hits = sum(1 for kw in _DENSITY_MODEL_HINTS if kw in lowered)
    sentence_count = _count_sentences(content)

    score = 0
    score += min(40, numeric_hits * 12)
    score += min(35, (entity_hits + model_hint_hits) * 8)
    score += min(25, max(sentence_count - 1, 0) * 7)
    return max(0, min(100, int(score)))


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


def _set_gate_meta(
    item: Any,
    *,
    stage: str,
    level: str,
    density: int,
    low_confidence: bool,
) -> None:
    try:
        setattr(item, "gate_stage", stage)
        setattr(item, "gate_level", level)
        setattr(item, "density_score", int(density))
        setattr(item, "is_soft_pass", stage == "SOFT_PASS")
        setattr(item, "low_confidence", bool(low_confidence))
    except Exception:
        pass


def _item_title_url(item: Any) -> tuple[str, str]:
    title = str(getattr(item, "title", "") or getattr(item, "item_id", "") or "").strip()
    url = str(getattr(item, "url", "") or "").strip()
    return title, url


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
            soft_density_pass=0,
            density_score_top5=[],
        )
        return [], {}, stats

    hard_rejected: dict[int, str] = {}
    candidates: list[int] = []
    density_rankings: list[tuple[str, str, int]] = []
    for idx, item in enumerate(items):
        body = _normalize(getattr(item, "body", ""))
        d_score = density_score(body)
        title, url = _item_title_url(item)
        density_rankings.append((title, url, d_score))
        hard_reason = _hard_reject_reason(body)
        fragment = _is_fragment_placeholder(body)
        if hard_reason:
            hard_rejected[idx] = hard_reason
            _set_rejected_reason(item, hard_reason)
            _set_gate_meta(item, stage="REJECT", level="hard", density=d_score, low_confidence=False)
        elif fragment:
            hard_rejected[idx] = "fragment_placeholder"
            _set_rejected_reason(item, "fragment_placeholder")
            _set_gate_meta(item, stage="REJECT", level="hard", density=d_score, low_confidence=False)
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
            _set_gate_meta(
                item,
                stage="HARD_PASS",
                level="strict",
                density=density_score(getattr(item, "body", "")),
                low_confidence=False,
            )
        else:
            strict_reject_idx.append(idx)
            strict_rejected_reasons[idx] = reason or "rejected_by_gate"

    relaxed_pass_idx: list[int] = []
    density_soft_pass_idx: list[int] = []
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
                _set_gate_meta(
                    item,
                    stage="SOFT_PASS",
                    level="relaxed",
                    density=density_score(getattr(item, "body", "")),
                    low_confidence=True,
                )
            else:
                d_score = density_score(getattr(item, "body", ""))
                if reason in {"content_too_short", "insufficient_sentences"} and d_score >= 35:
                    density_soft_pass_idx.append(idx)
                    _clear_rejected_reason(item)
                    _set_gate_meta(
                        item,
                        stage="SOFT_PASS",
                        level="density",
                        density=d_score,
                        low_confidence=True,
                    )
                    continue

                relaxed_reason = reason or "low_density_score"
                relaxed_rejected[idx] = relaxed_reason
                _set_rejected_reason(item, relaxed_reason)
                _set_gate_meta(item, stage="REJECT", level="soft", density=d_score, low_confidence=False)

        final_soft_rejected = relaxed_rejected

    kept_idx = strict_pass_idx + relaxed_pass_idx + density_soft_pass_idx
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
        soft_density_pass=len(density_soft_pass_idx),
        density_score_top5=sorted(density_rankings, key=lambda t: t[2], reverse=True)[:5],
    )
    return kept_items, rejected_map, stats
