"""Information-density scoring and gating utilities."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Any, Callable, Literal

try:
    from config import settings as _settings
except Exception:  # pragma: no cover - fallback for isolated tests
    _settings = None

DensityKind = Literal["event", "signal", "corp"]

_SENTENCE_SPLIT_RE = re.compile(r"[.!?。？！]+")
_ENTITY_TOKEN_RE = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9\-_]{2,}\b")
_MIXED_PROPER_NOUN_RE = re.compile(r"(?=.*[A-Za-z])(?=.*[\u4e00-\u9fff])[A-Za-z0-9\u4e00-\u9fff]{3,}")
_NUMERIC_RE = re.compile(
    r"(?:\b20\d{2}\b)"
    r"|(?:\bv?\d+(?:\.\d+)?(?:\.\d+)?\b)"
    r"|(?:\b\d+(?:\.\d+)?\s*(?:%|ms|s|sec|mins?|hours?|days?|gb|mb|tb|kb|tokens?|x)\b)"
    r"|(?:[$€¥]\s*\d+(?:\.\d+)?)"
    r"|(?:\b\d+(?:\.\d+)?\s*(?:usd|dollars?|元|萬|亿|億|b|m|k)\b)",
    re.IGNORECASE,
)
_FRAGMENT_DOTS_RE = re.compile(r"\b(?:was|is|are)\s*\.\.\.", re.IGNORECASE)
_TRAILING_CONNECTOR_RE = re.compile(
    r"(?:\b(?:and|or|but|with|for|to|of|in|on|at|by|from|that|which)\b|[，,])\s*$",
    re.IGNORECASE,
)

_DEFAULT_ENTITY_KEYWORDS = {
    "openai", "google", "microsoft", "amazon", "aws", "meta", "apple", "nvidia",
    "alibaba", "tencent", "bytedance", "baidu", "huawei",
    "chatgpt", "gpt", "claude", "gemini", "llama", "deepseek", "qwen", "mistral",
    "copilot", "langchain", "cursor", "github", "azure", "vertex", "bedrock",
}
_DEFAULT_BOILERPLATE_KEYWORDS = {
    "monitoring continues",
    "stay tuned",
    "overview",
    "highlights",
    "this shows",
    "roundup",
    "digest",
    "index",
    "weekly",
    "top links",
    "subscribe",
    "sign in",
    "login",
    "today no",
    "today there is no",
}


def _csv_set(raw: str | None, fallback: set[str]) -> set[str]:
    if not raw:
        return set(fallback)
    vals = {s.strip().lower() for s in raw.split(",") if s.strip()}
    return vals if vals else set(fallback)


ENTITY_KEYWORDS = _csv_set(
    getattr(_settings, "INFO_DENSITY_ENTITY_KEYWORDS", None),
    _DEFAULT_ENTITY_KEYWORDS,
)
BOILERPLATE_KEYWORDS = _csv_set(
    getattr(_settings, "INFO_DENSITY_BOILERPLATE_KEYWORDS", None),
    _DEFAULT_BOILERPLATE_KEYWORDS,
)


@dataclass(frozen=True)
class InfoDensityBreakdown:
    entity_hits: int
    numeric_hits: int
    sentence_count: int
    boilerplate_hits: int
    fragment_penalty: int
    score: int


@dataclass(frozen=True)
class DensityGateThreshold:
    min_score: int
    min_entities: int
    min_numeric: int
    min_sentences: int


@dataclass(frozen=True)
class DensityGateStats:
    total_in: int
    passed: int
    rejected_total: int
    avg_score: float
    rejected_by_reason: dict[str, int]
    rejected_reason_top: list[tuple[str, int]]


def _threshold(kind: DensityKind) -> DensityGateThreshold:
    if kind == "event":
        return DensityGateThreshold(
            min_score=int(getattr(_settings, "INFO_DENSITY_MIN_SCORE_EVENT", 55) if _settings else 55),
            min_entities=int(getattr(_settings, "INFO_DENSITY_MIN_ENTITY_EVENT", 2) if _settings else 2),
            min_numeric=int(getattr(_settings, "INFO_DENSITY_MIN_NUMERIC_EVENT", 1) if _settings else 1),
            min_sentences=int(getattr(_settings, "INFO_DENSITY_MIN_SENTENCES_EVENT", 3) if _settings else 3),
        )
    if kind == "signal":
        return DensityGateThreshold(
            min_score=int(getattr(_settings, "INFO_DENSITY_MIN_SCORE_SIGNAL", 35) if _settings else 35),
            min_entities=int(getattr(_settings, "INFO_DENSITY_MIN_ENTITY_SIGNAL", 1) if _settings else 1),
            min_numeric=int(getattr(_settings, "INFO_DENSITY_MIN_NUMERIC_SIGNAL", 0) if _settings else 0),
            min_sentences=int(getattr(_settings, "INFO_DENSITY_MIN_SENTENCES_SIGNAL", 2) if _settings else 2),
        )
    return DensityGateThreshold(
        min_score=int(getattr(_settings, "INFO_DENSITY_MIN_SCORE_CORP", 45) if _settings else 45),
        min_entities=int(getattr(_settings, "INFO_DENSITY_MIN_ENTITY_CORP", 1) if _settings else 1),
        min_numeric=int(getattr(_settings, "INFO_DENSITY_MIN_NUMERIC_CORP", 0) if _settings else 0),
        min_sentences=int(getattr(_settings, "INFO_DENSITY_MIN_SENTENCES_CORP", 2) if _settings else 2),
    )


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _sentence_parts(text: str) -> list[str]:
    return [p.strip() for p in _SENTENCE_SPLIT_RE.split(text) if p.strip()]


def _entity_hits(text: str) -> int:
    lowered = text.lower()
    keyword_hits = sum(1 for kw in ENTITY_KEYWORDS if kw in lowered)
    token_hits = len(set(_ENTITY_TOKEN_RE.findall(text)))
    mixed_hits = len(set(_MIXED_PROPER_NOUN_RE.findall(text)))
    return keyword_hits + token_hits + mixed_hits


def _numeric_hits(text: str) -> int:
    return len(_NUMERIC_RE.findall(text))


def _boilerplate_hits(text: str) -> int:
    lowered = text.lower()
    return sum(1 for kw in BOILERPLATE_KEYWORDS if kw in lowered)


def _fragment_penalty(text: str, sentences: list[str]) -> int:
    penalty = 0
    if _FRAGMENT_DOTS_RE.search(text):
        penalty += 1
    if _TRAILING_CONNECTOR_RE.search(text):
        penalty += 1
    short_sentences = [s for s in sentences if len(s) < 32]
    if sentences and (len(short_sentences) / len(sentences) > 0.6):
        penalty += 1
    return min(penalty, 2)


def info_density_breakdown(text: str) -> InfoDensityBreakdown:
    normalized = _normalize(text)
    if not normalized:
        return InfoDensityBreakdown(
            entity_hits=0,
            numeric_hits=0,
            sentence_count=0,
            boilerplate_hits=1,
            fragment_penalty=2,
            score=0,
        )

    sentences = _sentence_parts(normalized)
    entity_hits = _entity_hits(normalized)
    numeric_hits = _numeric_hits(normalized)
    sentence_count = len(sentences)
    boilerplate_hits = _boilerplate_hits(normalized)
    fragment_penalty = _fragment_penalty(normalized, sentences)

    score = 0
    score += 20 * min(3, entity_hits)
    score += 15 * min(3, numeric_hits)
    score += 10 * min(5, max(sentence_count - 1, 0))
    score -= 25 * min(2, boilerplate_hits)
    score -= 20 * min(2, fragment_penalty)
    score = max(0, min(100, int(score)))

    return InfoDensityBreakdown(
        entity_hits=entity_hits,
        numeric_hits=numeric_hits,
        sentence_count=sentence_count,
        boilerplate_hits=boilerplate_hits,
        fragment_penalty=fragment_penalty,
        score=score,
    )


def density_gate_reason(
    breakdown: InfoDensityBreakdown,
    kind: DensityKind,
) -> str | None:
    threshold = _threshold(kind)
    if breakdown.fragment_penalty > 0:
        return "fragment_placeholder"
    if breakdown.boilerplate_hits > 0 and breakdown.score < threshold.min_score:
        return "boilerplate"
    if breakdown.score < threshold.min_score:
        return "low_density_score"
    if breakdown.entity_hits < threshold.min_entities:
        return "missing_entities"
    if breakdown.numeric_hits < threshold.min_numeric:
        return "missing_numbers"
    if breakdown.sentence_count < threshold.min_sentences:
        return "insufficient_sentences"
    return None


def evaluate_text_density(
    text: str,
    kind: DensityKind,
) -> tuple[bool, str | None, InfoDensityBreakdown]:
    breakdown = info_density_breakdown(text)
    reason = density_gate_reason(breakdown, kind)
    return reason is None, reason, breakdown


def candidate_text_from_card(card: Any) -> str:
    parts: list[str] = []
    for attr in (
        "title_plain",
        "what_happened",
        "why_important",
        "one_liner",
        "technical_interpretation",
        "source_name",
        "category",
    ):
        val = getattr(card, attr, "")
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    for attr in ("fact_check_confirmed", "evidence_lines", "derivable_effects", "action_items"):
        val = getattr(card, attr, [])
        if isinstance(val, list):
            parts.extend(str(v).strip() for v in val[:5] if str(v).strip())
    return _normalize(" ".join(parts))


def gate_card_density(
    card: Any,
    kind: DensityKind,
) -> tuple[bool, str | None, InfoDensityBreakdown]:
    text = candidate_text_from_card(card)
    ok, reason, breakdown = evaluate_text_density(text, kind)
    try:
        setattr(card, "info_density_score", breakdown.score)
        setattr(card, "info_density_reason", reason or "")
    except Exception:
        pass
    return ok, reason, breakdown


def apply_density_gate(
    items: list[Any],
    kind: DensityKind,
    text_getter: Callable[[Any], str] | None = None,
) -> tuple[list[Any], dict[int, str], DensityGateStats, dict[int, InfoDensityBreakdown]]:
    if not items:
        return [], {}, DensityGateStats(0, 0, 0, 0.0, {}, []), {}

    passed: list[Any] = []
    rejected: dict[int, str] = {}
    breakdown_map: dict[int, InfoDensityBreakdown] = {}
    scores: list[int] = []

    for idx, item in enumerate(items):
        if text_getter is None:
            text = candidate_text_from_card(item)
        else:
            text = _normalize(text_getter(item))
        ok, reason, breakdown = evaluate_text_density(text, kind)
        breakdown_map[idx] = breakdown
        scores.append(breakdown.score)
        if ok:
            passed.append(item)
            try:
                setattr(item, "rejected_reason", "")
            except Exception:
                pass
        else:
            rejected_reason = reason or "low_density_score"
            rejected[idx] = rejected_reason
            try:
                setattr(item, "rejected_reason", rejected_reason)
            except Exception:
                pass

    reason_counter = Counter(rejected.values())
    stats = DensityGateStats(
        total_in=len(items),
        passed=len(passed),
        rejected_total=len(rejected),
        avg_score=round(sum(scores) / len(scores), 2) if scores else 0.0,
        rejected_by_reason=dict(reason_counter),
        rejected_reason_top=reason_counter.most_common(5),
    )
    return passed, rejected, stats, breakdown_map

