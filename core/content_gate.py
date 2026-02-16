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
EVENT_GATE_LEVEL: tuple[int, int] = (1200, 3)
SIGNAL_GATE_LEVEL: tuple[int, int] = (300, 2)

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

# ---------------------------------------------------------------------------
# AI topic relevance check
# ---------------------------------------------------------------------------
try:
    from config.settings import AI_TOPIC_KEYWORDS as _AI_KEYWORDS
except Exception:  # pragma: no cover
    _AI_KEYWORDS = [
        "ai", "llm", "agent", "model", "inference", "gpu", "nvidia", "openai",
        "anthropic", "google", "microsoft", "aws", "meta", "deepseek", "qwen",
        "rag", "vector", "vllm", "transformer", "multimodal", "copilot",
        "gemini", "claude", "gpt", "chatgpt", "llama", "mistral",
    ]


def _build_ai_re() -> re.Pattern[str]:
    """Build a regex that matches AI keywords with word boundaries."""
    # Sort by length descending so longer keywords match first
    sorted_kw = sorted(_AI_KEYWORDS, key=len, reverse=True)
    escaped = [re.escape(kw) for kw in sorted_kw]
    pattern = r"(?:^|\b| )" + r"(?:" + r"|".join(escaped) + r")" + r"(?:\b| |$)"
    return re.compile(pattern, re.IGNORECASE)


_AI_RE = _build_ai_re()


def is_ai_relevant(title: str, body: str) -> bool:
    """Return True if the content is AI-related (matches at least one keyword)."""
    combined = f"{title} {body}"
    return bool(_AI_RE.search(combined))


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


@dataclass(frozen=True)
class SplitGateStats:
    total: int
    event_gate_pass_total: int
    signal_gate_pass_total: int
    rejected_total: int
    rejected_by_reason: dict[str, int]
    rejected_reason_top: list[tuple[str, int]]


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
        elif not is_ai_relevant(title, body):
            hard_rejected[idx] = "non_ai_topic"
            _set_rejected_reason(item, "non_ai_topic")
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


def apply_split_content_gate(
    items: list[Any],
    *,
    event_level: tuple[int, int] = EVENT_GATE_LEVEL,
    signal_level: tuple[int, int] = SIGNAL_GATE_LEVEL,
) -> tuple[list[Any], list[Any], dict[int, str], SplitGateStats]:
    """Apply split gates for Event and Signal pools.

    - Event gate: stricter thresholds, used for event candidate pipeline.
    - Signal gate: lower thresholds, used for signal/corp fallback pool.
    - Hard reject rules always apply equally to both gates.
    """
    total = len(items)
    if total == 0:
        stats = SplitGateStats(
            total=0,
            event_gate_pass_total=0,
            signal_gate_pass_total=0,
            rejected_total=0,
            rejected_by_reason={},
            rejected_reason_top=[],
        )
        return [], [], {}, stats

    event_min_len, event_min_sentences = event_level
    signal_min_len, signal_min_sentences = signal_level
    # Prevent gate starvation on short-but-informative posts while keeping
    # hard rejects and fragment filtering strict.
    signal_soft_min_len = max(180, int(signal_min_len * 0.6))
    signal_soft_min_sentences = max(2, signal_min_sentences - 1)
    signal_soft_min_density = 30
    signal_fallback_min_len = max(80, int(signal_min_len * 0.25))
    signal_fallback_min_sentences = 1
    signal_fallback_min_density = 16

    event_pass_idx: list[int] = []
    signal_pass_idx: list[int] = []
    rejected_map: dict[int, str] = {}
    signal_fallback_candidates: list[tuple[int, int, str | None]] = []

    for idx, item in enumerate(items):
        body = _normalize(getattr(item, "body", ""))
        title = str(getattr(item, "title", "") or "")
        d_score = density_score(body)

        hard_reason = _hard_reject_reason(body)
        if hard_reason:
            rejected_map[idx] = hard_reason
            _set_rejected_reason(item, hard_reason)
            _set_gate_meta(item, stage="REJECT", level="hard", density=d_score, low_confidence=False)
            try:
                setattr(item, "event_gate_pass", False)
                setattr(item, "signal_gate_pass", False)
            except Exception:
                pass
            continue

        if _is_fragment_placeholder(body):
            rejected_map[idx] = "fragment_placeholder"
            _set_rejected_reason(item, "fragment_placeholder")
            _set_gate_meta(item, stage="REJECT", level="hard", density=d_score, low_confidence=False)
            try:
                setattr(item, "event_gate_pass", False)
                setattr(item, "signal_gate_pass", False)
            except Exception:
                pass
            continue

        if not is_ai_relevant(title, body):
            rejected_map[idx] = "non_ai_topic"
            _set_rejected_reason(item, "non_ai_topic")
            _set_gate_meta(item, stage="REJECT", level="hard", density=d_score, low_confidence=False)
            try:
                setattr(item, "event_gate_pass", False)
                setattr(item, "signal_gate_pass", False)
            except Exception:
                pass
            continue

        event_ok, event_reason = is_valid_article(
            body,
            content_min_len=event_min_len,
            min_sentences=event_min_sentences,
        )
        signal_ok, signal_reason = is_valid_article(
            body,
            content_min_len=signal_min_len,
            min_sentences=signal_min_sentences,
        )
        sentence_count = _count_sentences(body)
        body_len = len(body)

        if event_ok:
            event_pass_idx.append(idx)
            signal_pass_idx.append(idx)
            _clear_rejected_reason(item)
            _set_gate_meta(item, stage="EVENT_PASS", level="event", density=d_score, low_confidence=False)
            try:
                setattr(item, "event_gate_pass", True)
                setattr(item, "signal_gate_pass", True)
            except Exception:
                pass
            continue

        if signal_ok:
            signal_pass_idx.append(idx)
            _clear_rejected_reason(item)
            _set_gate_meta(item, stage="SIGNAL_PASS", level="signal", density=d_score, low_confidence=True)
            try:
                setattr(item, "event_gate_pass", False)
                setattr(item, "signal_gate_pass", True)
                setattr(item, "event_rejected_reason", event_reason or "event_gate_reject")
            except Exception:
                pass
            continue

        # Adaptive soft-pass for signal pool only:
        # allow shorter snippets when they still contain measurable density.
        if (
            signal_reason in {"content_too_short", "insufficient_sentences"}
            and body_len >= signal_soft_min_len
            and sentence_count >= signal_soft_min_sentences
            and d_score >= signal_soft_min_density
        ):
            signal_pass_idx.append(idx)
            _clear_rejected_reason(item)
            _set_gate_meta(item, stage="SIGNAL_PASS", level="signal_soft", density=d_score, low_confidence=True)
            try:
                setattr(item, "event_gate_pass", False)
                setattr(item, "signal_gate_pass", True)
                setattr(item, "event_rejected_reason", event_reason or "event_gate_reject")
                setattr(item, "signal_soft_pass", True)
            except Exception:
                pass
            continue

        if (
            signal_reason in {"content_too_short", "insufficient_sentences"}
            and body_len >= signal_fallback_min_len
            and sentence_count >= signal_fallback_min_sentences
            and d_score >= signal_fallback_min_density
        ):
            signal_fallback_candidates.append((idx, d_score, event_reason))

        reason = signal_reason or event_reason or "rejected_by_gate"
        rejected_map[idx] = reason
        _set_rejected_reason(item, reason)
        _set_gate_meta(item, stage="REJECT", level="split", density=d_score, low_confidence=False)
        try:
            setattr(item, "event_gate_pass", False)
            setattr(item, "signal_gate_pass", False)
        except Exception:
            pass

    # Starvation guard: when no signal passed, promote top dense short items
    # (still excluding hard rejects/fragments handled above).
    if not signal_pass_idx and signal_fallback_candidates:
        signal_fallback_candidates.sort(key=lambda row: row[1], reverse=True)
        for idx, d_score, event_reason in signal_fallback_candidates[:3]:
            signal_pass_idx.append(idx)
            rejected_map.pop(idx, None)
            item = items[idx]
            _clear_rejected_reason(item)
            _set_gate_meta(item, stage="SIGNAL_PASS", level="signal_fallback", density=d_score, low_confidence=True)
            try:
                setattr(item, "event_gate_pass", False)
                setattr(item, "signal_gate_pass", True)
                setattr(item, "event_rejected_reason", event_reason or "event_gate_reject")
                setattr(item, "signal_soft_pass", True)
            except Exception:
                pass

    event_candidates = [items[idx] for idx in event_pass_idx]
    signal_pool = [items[idx] for idx in signal_pass_idx]

    rejected_by_reason = dict(Counter(rejected_map.values()))
    rejected_reason_top = sorted(rejected_by_reason.items(), key=lambda kv: kv[1], reverse=True)[:5]
    stats = SplitGateStats(
        total=total,
        event_gate_pass_total=len(event_pass_idx),
        signal_gate_pass_total=len(signal_pass_idx),
        rejected_total=len(rejected_map),
        rejected_by_reason=rejected_by_reason,
        rejected_reason_top=rejected_reason_top,
    )
    return event_candidates, signal_pool, rejected_map, stats
