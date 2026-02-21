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
    "english_heavy_paragraphs_fixed_count": 0,   # backward-compat alias
    "english_heavy_skeletonized_count": 0,        # new: tracks zh_skeletonize calls
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
    _stats["english_heavy_skeletonized_count"] = 0
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


def zh_skeletonize_if_english_heavy(
    text: str,
    context: "dict | None" = None,
) -> str:
    """Produce 2–3 deterministic ZH skeleton sentences for English-heavy paragraphs.

    Condition: ASCII (non-space) ratio > 60 % AND CJK char count < 12 AND len >= 15.

    Output format (key tokens preserved, no facts invented):
        事件：{entity} {verb_zh}（{number}）。
        影響：{impact_text}。
        [證據：{proof_token}。]   # only when a proof token is found

    Args:
        text:    Input text (English or lightly ZH-mixed).
        context: Optional dict with card-level hints:
                   "title"       (str) — for entity extraction
                   "why"         (str) — why_important for impact sentence
                   "proof_token" (str) — pre-extracted hard evidence token

    Returns the original text unchanged when the condition is not met.
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

    # --- Extract entities and numbers from the text (no fabrication) ---
    nouns = extract_proper_nouns(stripped)
    numbers = re.findall(r"[$€£¥]?\d+(?:\.\d+)?[%BMKbmk]?|\bv\d+\.\d+", stripped)

    # --- Sentence 1: Event verb ---
    action_lower = stripped.lower()
    if any(k in action_lower for k in ("release", "launch", "publish", "ship", "debut")):
        verb_zh = "發布"
    elif any(k in action_lower for k in ("announce", "reveal", "unveil", "introduce")):
        verb_zh = "宣布"
    elif any(k in action_lower for k in ("fund", "raise", "invest", "capital", "billion", "million", "series")):
        verb_zh = "宣布融資"
    elif any(k in action_lower for k in ("acqui", "merger")):
        verb_zh = "宣布收購"
    elif any(k in action_lower for k in ("partner", "deal", "agreement")):
        verb_zh = "達成合作"
    elif any(k in action_lower for k in ("benchmark", "score", "achiev", "outperform", "surpass", "sota")):
        verb_zh = "達到新里程碑"
    elif any(k in action_lower for k in ("model", "train", "inference", "gpu", "chip", "hardware")):
        verb_zh = "推出新模型"
    elif any(k in action_lower for k in ("open source", "open-source", "github")):
        verb_zh = "開源發布"
    else:
        verb_zh = "更新"

    # Entity: prefer context title, then first extracted proper noun, then fallback
    if context and context.get("title"):
        title_nouns = extract_proper_nouns(str(context["title"]))
        entity = title_nouns[0] if title_nouns else (nouns[0] if nouns else "相關方")
    else:
        entity = nouns[0] if nouns else "相關方"

    s1 = (f"事件：{entity} {verb_zh}（{numbers[0]}）。" if numbers
          else f"事件：{entity} {verb_zh}。")

    # --- Sentence 2: Impact ---
    s2_text = ""
    if context and context.get("why"):
        why_raw = str(context["why"]).strip()
        if sum(1 for c in why_raw if "\u4e00" <= c <= "\u9fff") >= 4:
            s2_text = why_raw[:60]
    if not s2_text:
        if "融資" in verb_zh or any(k in action_lower for k in ("billion", "million", "fund")):
            s2_text = "可能加速產品研發並改變競爭格局"
        elif "模型" in verb_zh or "benchmark" in action_lower or "score" in action_lower:
            s2_text = "將推動技術生態演進，影響採用成本與競爭"
        elif any(k in verb_zh for k in ("收購", "合作")):
            s2_text = "可能重塑市場競爭格局"
        else:
            s2_text = "可能影響採用決策、成本結構及競爭態勢"
    s2 = f"影響：{s2_text}。"

    # --- Sentence 3 (optional): Proof token ---
    proof_token = ""
    if context and context.get("proof_token"):
        proof_token = str(context["proof_token"]).strip()
    if not proof_token:
        for _pat in (
            re.compile(r"\bv\d+(?:\.\d+)+\b", re.IGNORECASE),
            re.compile(r"\$\d+(?:\.\d+)?[MB]\b"),
            re.compile(r"\b\d+(?:\.\d+)?\s*[BM]\b"),
            re.compile(r"\b20\d{2}-\d{2}-\d{2}\b"),
        ):
            _m = _pat.search(stripped)
            if _m:
                proof_token = _m.group()
                break

    parts = [s1, s2]
    if proof_token:
        parts.append(f"證據：{proof_token}。")

    _stats["english_heavy_skeletonized_count"] += 1
    _stats["english_heavy_paragraphs_fixed_count"] += 1  # backward-compat
    return " ".join(parts)


def ensure_not_all_english(text: str) -> str:
    """Backward-compat alias for zh_skeletonize_if_english_heavy (no context)."""
    return zh_skeletonize_if_english_heavy(text)


def normalize_exec_text(
    text: str,
    glossary: "dict | None" = None,
    seen: "set | None" = None,
    context: "dict | None" = None,
) -> str:
    """Full normalization pipeline: glossary annotation → ZH skeletonization.

    Args:
        text:     Input text to normalize.
        glossary: Proper noun glossary dict; loads from default path if None.
        seen:     Shared seen-set across fields of the same event (term dedup).
        context:  Optional card-level hints passed to zh_skeletonize_if_english_heavy
                  (keys: "title", "why", "proof_token").

    Returns:
        Normalized text safe for PPT / DOCX insertion.
    """
    if not text:
        return text
    if glossary is None:
        glossary = load_glossary()
    t = apply_glossary(text, glossary, seen)
    t = zh_skeletonize_if_english_heavy(t, context)
    return t
