"""EN-ZH Hybrid Glossing v1 — stdlib only.

Allows EN-ZH mixed text in deck; prohibits all-English paragraphs.
Proper nouns keep their English form but receive a 繁中 annotation on first
occurrence within a text field (or a shared cross-field `seen` set).
A ZH skeleton is injected when text is too English-heavy.

Hard limits: no schema changes, no pip dependencies, no LLM / API calls.

Public API
----------
load_glossary()          — load / cache config/proper_noun_glossary.json
extract_proper_nouns()   — regex-based capitalised-token extractor
apply_glossary()         — first-occurrence ZH annotation
ensure_not_all_english() — prepend ZH skeleton when ASCII ratio > 60% + ZH < 12
normalize_exec_text()    — combined pipeline (apply_glossary → ensure_not_all_english)
reset_gloss_stats()      — zero the per-run counters
get_gloss_stats()        — return a snapshot of the current counters
"""
from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Module-level stats counters — zeroed by reset_gloss_stats() each pipeline run
# ---------------------------------------------------------------------------

_stats: dict = {
    "english_heavy_paragraphs_fixed_count": 0,
    "proper_noun_gloss_applied_count": 0,
}

# Lazy-loaded glossary cache; populated on first call to load_glossary()
_GLOSSARY_CACHE: dict | None = None

# ---------------------------------------------------------------------------
# Big Tech / AI Lab companies — first occurrence is NOT annotated.
# These names are globally recognized; adding a ZH parenthetical creates
# clutter rather than clarity.  Other proper nouns (benchmarks, tools,
# frameworks) still receive first-occurrence annotations from the glossary.
# ---------------------------------------------------------------------------

NO_GLOSS_TERMS: frozenset = frozenset({
    "OpenAI", "NVIDIA", "Microsoft", "Google", "Anthropic",
    "AWS", "Meta", "Apple", "Intel", "xAI",
})

# Lowercase version for case-insensitive lookup against glossary keys
_NO_GLOSS_LOWER: frozenset = frozenset(t.lower() for t in NO_GLOSS_TERMS)

# ---------------------------------------------------------------------------
# Tokens that must NOT receive a ZH annotation even when capitalised
# ---------------------------------------------------------------------------

_FALSE_POSITIVES: frozenset = frozenset({
    # Role / organisational abbreviations
    "CEO", "CTO", "CFO", "COO", "CXO", "VP", "PM", "HR",
    # Technical acronyms already widely understood
    "AI", "ML", "DL", "RL", "CV", "NLP", "NLU", "LLM", "GPU", "CPU", "TPU",
    "API", "SDK", "CLI", "CI", "CD", "SaaS", "PaaS", "IaaS",
    # Legal / corporate suffixes
    "Inc", "Corp", "Ltd", "LLC", "Co",
    # Quarter / phase markers used in deck headers
    "Q1", "Q2", "Q3", "Q4", "T1", "T2", "T3", "T4",
})


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def reset_gloss_stats() -> None:
    """Reset per-run counters to zero.  Call once at the start of each pipeline run."""
    _stats["english_heavy_paragraphs_fixed_count"] = 0
    _stats["proper_noun_gloss_applied_count"] = 0


def get_gloss_stats() -> dict:
    """Return a copy of the current stats counters."""
    return dict(_stats)


def load_glossary(path: "Path | str | None" = None) -> dict:
    """Load proper_noun_glossary.json and cache the result.

    Returns an empty dict silently on any I/O or parse error so that the
    pipeline degrades gracefully when the config file is absent.
    """
    global _GLOSSARY_CACHE
    if _GLOSSARY_CACHE is not None:
        return _GLOSSARY_CACHE
    import json
    if path is None:
        path = Path(__file__).resolve().parent.parent / "config" / "proper_noun_glossary.json"
    try:
        _GLOSSARY_CACHE = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        _GLOSSARY_CACHE = {}
    return _GLOSSARY_CACHE


def extract_proper_nouns(text: str) -> list[str]:
    """Return capitalised tokens (>= 3 chars) that are likely proper nouns.

    Multi-word capitalised phrases ('Google DeepMind') are kept as a single
    entry.  _FALSE_POSITIVES and pure-abbreviation tokens are excluded.
    """
    # Match one or more consecutive CamelCase / UPPER / Title-case words
    tokens = re.findall(
        r'\b[A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+)*\b',
        text,
    )
    result: list[str] = []
    seen_lower: set[str] = set()
    for t in tokens:
        if t in _FALSE_POSITIVES:
            continue
        if len(t.replace(" ", "")) < 3:
            continue
        t_lo = t.lower()
        if t_lo not in seen_lower:
            seen_lower.add(t_lo)
            result.append(t)
    return result


def apply_glossary(text: str, glossary: dict, seen: "set | None" = None) -> str:
    """Annotate the first occurrence of each glossary term with its ZH explanation.

    Args:
        text:       Input text (English or ZH-mixed).
        glossary:   Dict mapping English term → ZH annotation string.
        seen:       Mutable set tracking already-annotated term keys.
                    Pass the **same** set across multiple fields of one event
                    to prevent the same term being annotated twice per card.

    Returns:
        Text with first-occurrence annotations, e.g.
        ``"OpenAI（開放人工智慧） raises $6.6B"``

    Notes:
        * Longer terms are matched before shorter ones to avoid partial matches
          (e.g. "Google DeepMind" before "Google").
        * If the term is already followed by a 「（」 in the text it is assumed
          to have been annotated in a previous pass and is skipped.
    """
    if seen is None:
        seen = set()
    if not glossary or not text:
        return text

    result = text
    # Process longest terms first to prevent partial-match clobbering
    for term, zh_annotation in sorted(glossary.items(), key=lambda kv: -len(kv[0])):
        term_key = term.lower()
        if term_key in seen:
            continue
        # Big Tech / AI Lab companies: never annotate, regardless of glossary entry
        if term_key in _NO_GLOSS_LOWER:
            continue
        # Skip if already annotated (previous pass inserted 「term（」)
        if re.search(r'\b' + re.escape(term) + r'（', result, re.IGNORECASE):
            seen.add(term_key)
            continue
        pattern = re.compile(r'\b' + re.escape(term) + r'\b', re.IGNORECASE)
        match = pattern.search(result)
        if match:
            original = match.group(0)
            replacement = f"{original}（{zh_annotation}）"
            result = result[: match.start()] + replacement + result[match.end() :]
            seen.add(term_key)
            _stats["proper_noun_gloss_applied_count"] += 1

    return result


def ensure_not_all_english(text: str) -> str:
    """Prepend a ZH skeleton when a paragraph is overwhelmingly English.

    Condition: ASCII (non-space) ratio > 60 % **and** CJK char count < 12.
    The skeleton is built from entities and numbers already present in *text*
    so no facts are fabricated.

    Returns the original text unchanged when:
      * It already has sufficient ZH content.
      * It is shorter than 15 characters (likely a fragment — leave to semantic_guard_text).
      * It is empty or whitespace-only.
    """
    if not text or not text.strip():
        return text

    stripped = text.strip()

    # Very short text is likely a fragment — do not wrap
    if len(stripped) < 15:
        return text

    # Character counts
    ascii_nonspace = sum(1 for c in stripped if ord(c) < 128 and c.strip())
    zh_chars = sum(1 for c in stripped if "\u4e00" <= c <= "\u9fff")
    total_nonspace = sum(1 for c in stripped if c.strip())

    if total_nonspace == 0:
        return text

    ascii_ratio = ascii_nonspace / total_nonspace

    # Sufficient ZH content — no intervention needed
    if ascii_ratio <= 0.60 or zh_chars >= 12:
        return text

    # Extract existing entities and numbers (no fabrication)
    nouns = extract_proper_nouns(stripped)
    numbers = re.findall(r"[$€£¥]?\d+(?:\.\d+)?[%BMKbmk]?|\bv\d+\.\d+", stripped)

    # Detect action type from English keywords
    action_lower = stripped.lower()
    if any(k in action_lower for k in ("release", "launch", "publish", "ship", "announce", "debut")):
        action_zh = "發布新版本或重要公告"
    elif any(k in action_lower for k in ("fund", "raise", "invest", "capital", "billion", "million", "series")):
        action_zh = "完成新一輪融資或投資"
    elif any(k in action_lower for k in ("acqui", "merger", "deal", "partner", "agreement")):
        action_zh = "達成重要合作或收購協議"
    elif any(k in action_lower for k in ("benchmark", "score", "achiev", "outperform", "surpass", "sota")):
        action_zh = "在基準測試中取得優異成績"
    elif any(k in action_lower for k in ("model", "train", "inference", "gpu", "chip", "hardware")):
        action_zh = "推出新的 AI 模型或硬體"
    elif any(k in action_lower for k in ("open source", "open-source", "github")):
        action_zh = "開源或公開發布"
    else:
        action_zh = "有新進展"

    # Build skeleton referencing only existing entities / numbers
    entity = nouns[0] if nouns else "AI 相關方"
    if numbers:
        skeleton = f"【{entity}{action_zh}（{numbers[0]}）】 {stripped}"
    else:
        skeleton = f"【{entity}{action_zh}】 {stripped}"

    _stats["english_heavy_paragraphs_fixed_count"] += 1
    return skeleton


def normalize_exec_text(
    text: str,
    glossary: "dict | None" = None,
    seen: "set | None" = None,
) -> str:
    """Full normalization pipeline: glossary annotation → English-heavy detection.

    Args:
        text:     Input text to normalize.
        glossary: Proper noun glossary dict; loads from default path if None.
        seen:     Shared seen-set across fields of the same event (term dedup).

    Returns:
        Normalized text safe for PPT / DOCX insertion.
    """
    if not text:
        return text
    if glossary is None:
        glossary = load_glossary()
    t = apply_glossary(text, glossary, seen)
    t = ensure_not_all_english(t)
    return t
