"""utils/news_anchor.py — Concrete anchor extractor for AI news events.

Stdlib-only. No new pip deps. No API calls.

Extracts verifiable "anchor" tokens from raw news text: model versions, money
amounts, parameter counts, benchmarks, and product/model names.  These anchors
are then injected into newsroom_zh_rewrite v2 to prevent generic output.

Rules (only tokens that exist verbatim in source text):
  1. version   — v[N]+, model family + version (GPT-4.5, Llama-3.1-70B ...)
  2. money     — $Xm/b, Xbn, 億元, 萬美元 ...
  3. params    — N(B|M) where B>=1 or M>=100 (AI scale only)
  4. benchmark — MMLU / GPQA / SWE-bench / Arena / leaderboard / 評測 ...
  5. product   — known model families (conservative; passes stopword blacklist)
  6. metric    — "Nx faster", "Y% reduction", time ranges, GitHub PR numbers

Fallback: if none of the above → has_anchor=False (anchor_missing=True in payload)

Public API
----------
    extract_anchors(title, anchor_text, source_name, published_at) -> dict
        returns: {"anchors": list[str], "anchor_types": dict, "has_anchor": bool}

    pick_primary_anchor(anchors, anchor_types) -> str | None
        priority: model_with_version > benchmark > money > params > version > metric > product
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Known model families + version regex (conservative)
# ---------------------------------------------------------------------------

# Matches "GPT-4.5", "Claude 3.7 Sonnet", "Llama-3.1-70B", "Qwen2.5-72B",
# "DeepSeek-R1", "DeepSeek-V3", "Phi-3.5", "Gemini 2.0 Flash", "Grok-1.5",
# "Mistral-7B", "Sora", "Whisper v3"
_MODEL_VERSION_RE = re.compile(
    r"(?:"
    # GPT: GPT-4, GPT-4o, GPT-4 Turbo, GPT-4o mini, GPT-4.5 Preview
    r"GPT[-\s]*\d+(?:\.\d+)*o?(?:[-\s]+(?:mini|turbo|nano|preview|latest|realtime|vision|omni))?"
    # Claude: Claude 3.7 Sonnet, Claude 3 Haiku, Claude 3.5 Sonnet
    r"|Claude[-\s]+\d+(?:\.\d+)*(?:[-\s]+(?:Sonnet|Haiku|Opus|Instant|Core|Edge|Micro|Plus|Pro))?"
    # Gemini: Gemini 2.0 Flash, Gemini 1.5 Pro Experimental
    r"|Gemini[-\s]+\d+(?:\.\d+)*(?:[-\s]+(?:Flash|Pro|Ultra|Nano|Advanced|Experimental|Preview))?"
    # Llama: Llama-3.1-70B, Llama 3 8B, Llama-3.1-70B-Instruct
    r"|Llama[-\s]+\d+(?:\.\d+)*(?:[-.\s]+\d+[BM](?:it)?)?"
    # Qwen: Qwen2.5, Qwen2.5-72B
    r"|Qwen\s*\d+(?:\.\d+)*(?:[-\s]+\d+[BM])?"
    # DeepSeek: DeepSeek-R1, DeepSeek-V3, DeepSeek-Coder
    r"|DeepSeek[-\s]*(?:R|V|Coder|Chat|MoE)?(?:\d+(?:\.\d+)*)?(?:[-\s]+(?:Zero|Light|Distill|Instruct|Base|Chat|Preview)){0,2}"
    # Phi: Phi-3.5, Phi-4, Phi-4 mini
    r"|Phi[-\s]+\d+(?:\.\d+)*(?:[-\s]+(?:mini|small|medium|vision))?"
    # Mistral: Mistral-7B, Mistral Large, Mistral Nemo
    r"|Mistral[-\s]*(?:\d+(?:x\d+)?[BM]?)?(?:[-\s]+(?:Large|Small|Nemo|Instruct|Codestral))?"
    # Grok: Grok-1.5, Grok-2, Grok-2 mini
    r"|Grok[-\s]*\d+(?:[-\s]+(?:mini|vision|heavy))?"
    # Fixed / version-tagged
    r"|Sora[-\s]*(?:v\d+(?:\.\d+)*)?"
    r"|Whisper[-\s]*(?:v\d+(?:\.\d+)*)?"
    r"|TensorRT[-\-]*LLM"
    r"|vLLM"
    r"|llama\.cpp"
    r"|DALL[-\-]*E[-\s]*\d*"
    r"|Codex[-\s]*\d*"
    r"|Copilot[-\s]*\w*"
    r"|Cursor[-\s]*\d*"
    r"|Qwen\d*[-\s]*ASR"
    r")",
    re.IGNORECASE,
)

# Generic version numbers: v1.2.3, 2024.1
_GENERIC_VERSION_RE = re.compile(
    r"\bv\d+(?:\.\d+){1,3}\b"
    r"|\b\d{4}\.\d{1,2}\b"
    r"|\b(?:R|V)\d{1,2}\b",   # R1, V3
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Money regex  (must come before params to avoid double-counting)
# ---------------------------------------------------------------------------

_MONEY_RE = re.compile(
    r"\$[\d,]+(?:\.\d+)?\s*(?:billion|million|trillion|B|M|T)\b"
    r"|\b\d+(?:\.\d+)?\s*(?:billion|million)\s*(?:dollars?|USD)?"
    r"|\b\d+億(?:元|美元|港幣|人民幣)"
    r"|\b\d+萬美元"
    r"|\b\d+(?:\.\d+)?\s*兆",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Parameter count regex  (AI model scale: B>=1, M>=100)
# ---------------------------------------------------------------------------

_PARAMS_RE = re.compile(
    # Must NOT be preceded by $ (would be money)
    r"(?<!\$)\b(\d+(?:\.\d+)?)\s*([BM])\s*(?:parameter|param|token|model|weights?)\b"
    r"|(?<!\$)\b(\d+(?:\.\d+)?)\s*(B)\b(?!\s*(?:illion|yte|illion-dollar))",
    re.IGNORECASE,
)

# Simpler: explicit param context
_PARAMS_EXPLICIT_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(?:billion|B)\s+(?:parameter|param)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Benchmark keywords
# ---------------------------------------------------------------------------

_BENCHMARK_KEYWORDS: list[str] = [
    "MMLU", "GPQA", "GPQA Diamond", "SWE-bench", "SWE-Bench", "SWEbench",
    "HumanEval", "HumanEval+", "MATH", "MATH-500", "HellaSwag", "WinoGrande",
    "ARC-Challenge", "GSM8K", "TruthfulQA", "BigBench", "BIG-Bench",
    "MT-Bench", "AlpacaEval", "HELM", "LMSys", "Chatbot Arena",
    "LiveBench", "AIME", "AMC", "leaderboard", "benchmark",
    "基準測試", "評測", "跑分", "排行榜", "基準",
    "Elo score", "arena score",
]

# ---------------------------------------------------------------------------
# Performance metric regex
# ---------------------------------------------------------------------------

_METRIC_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*x\s+(?:faster|cheaper|slower|better|more efficient)"
    r"|\b\d+(?:\.\d+)?%\s+(?:faster|cheaper|lower|reduction|improvement|increase|higher|decrease|better)"
    r"|\b(?:faster|cheaper|lower|reduced)\s+by\s+\d+(?:\.\d+)?%"
    r"|\b\d+(?:\.\d+)?%\s+(?:cost|latency|throughput|performance|accuracy)"
    r"|\b\d+(?:\.\d+)?\s*倍(?:提升|加速|降低|更快|更便宜)"
    r"|\b提升\d+(?:\.\d+)?%"
    r"|\b降低\d+(?:\.\d+)?%"
    # Time-range anchors: "6 to 12 months", "3 to 6 weeks", "1 to 2 years"
    r"|\b\d+\s+to\s+\d+\s+(?:months?|weeks?|years?|days?)\b"
    # GitHub PR / issue numbers (verbatim in commit titles/PR descriptions)
    r"|#\d{4,}\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Stopword blacklist for product/model name validation
# ---------------------------------------------------------------------------

_STOPWORD_SET: set[str] = {
    "the", "this", "that", "one", "two", "three", "four", "five",
    "east", "west", "north", "south", "new", "old", "big", "small",
    "open", "show", "help", "needed", "add", "fix", "dual", "phase",
    "mixed", "how", "why", "when", "where", "latest", "update", "guide",
    "feature", "features", "news", "blog", "hn", "recent", "top", "best",
    "good", "great", "fast", "free", "real", "time", "realtime", "let",
    "text", "speak", "talk", "boom", "just", "end", "use", "choice",
    "same", "new", "any", "via", "for", "and", "but", "with", "from",
}

# ---------------------------------------------------------------------------
# Priority ordering for pick_primary_anchor
# ---------------------------------------------------------------------------

# Type priority (higher = more specific / better anchor)
_TYPE_PRIORITY: dict[str, int] = {
    "product": 6,    # model with version (most specific)
    "benchmark": 5,
    "money": 4,
    "params": 3,
    "version": 2,
    "metric": 1,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clean_match(s: str) -> str:
    return s.strip().rstrip(".,;:!?")


def _passes_stopword(name: str) -> bool:
    """Return True if name does NOT consist entirely of stopwords."""
    words = re.sub(r"[^a-zA-Z\s]", " ", name).split()
    meaningful = [w for w in words if w.lower() not in _STOPWORD_SET and len(w) > 1]
    return len(meaningful) >= 1


def _dedup_anchors(anchors: list[str]) -> list[str]:
    """Remove duplicates (case-insensitive substring check)."""
    seen: list[str] = []
    for a in anchors:
        a_lower = a.lower()
        if not any(a_lower in s.lower() or s.lower() in a_lower for s in seen):
            seen.append(a)
    return seen


def _extract_params(src: str) -> list[str]:
    """Extract parameter counts with AI-scale validation."""
    results: list[str] = []

    # Explicit pattern: "397 billion parameters", "70B parameters"
    for m in _PARAMS_EXPLICIT_RE.finditer(src):
        val = float(m.group(1).replace(",", ""))
        results.append(f"{m.group(1)}B")

    # Standalone B/M (no explicit "parameter" word)
    for m in re.finditer(
        r"(?<!\$)\b(\d+(?:\.\d+)?)\s*(B)\b(?!\s*(?:illion\b|yte\b|road\b|ase\b))",
        src, re.IGNORECASE,
    ):
        val = float(m.group(1).replace(",", ""))
        if val >= 1.0:  # >=1B suggests AI scale, not e.g. "1B users"
            token = f"{m.group(1)}B"
            # Only add if not already captured by explicit pattern
            if not any(token in r for r in results):
                results.append(token)

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_anchors(
    title: str = "",
    anchor_text: str = "",
    source_name: str = "",
    published_at: str = "",
) -> dict:
    """Extract concrete anchors from news text.

    Parameters
    ----------
    title       : article headline (most important)
    anchor_text : combined body text (what_happened + evidence_lines)
    source_name : source name (for fallback)
    published_at: ISO date string (for fallback)

    Returns
    -------
    {
        "anchors":      list[str],   # up to 5, de-duped, ordered by priority
        "anchor_types": dict,        # {type: count}
        "has_anchor":   bool,        # True if any real anchor found
    }
    """
    src = (title.strip() + " " + anchor_text.strip()).strip()
    if not src:
        return {"anchors": [], "anchor_types": {}, "has_anchor": False}

    typed_anchors: list[tuple[int, str, str]] = []  # (priority, type, value)
    seen_values: set[str] = set()

    def _add(priority: int, atype: str, value: str) -> None:
        v = _clean_match(value)
        if not v:
            return
        vk = v.lower()
        if vk not in seen_values and len(v) >= 2:
            seen_values.add(vk)
            typed_anchors.append((priority, atype, v))

    # 1. Model/product with version (highest priority)
    for m in _MODEL_VERSION_RE.finditer(src):
        candidate = _clean_match(m.group(0))
        if candidate and _passes_stopword(candidate) and len(candidate) >= 3:
            _add(_TYPE_PRIORITY["product"] * 100 + len(candidate), "product", candidate)

    # 2. Benchmark keywords
    for kw in _BENCHMARK_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", src, re.IGNORECASE):
            _add(_TYPE_PRIORITY["benchmark"] * 100, "benchmark", kw)
            break  # one benchmark per event is enough

    # 3. Money amounts
    for m in _MONEY_RE.finditer(src):
        candidate = _clean_match(m.group(0))
        if candidate:
            _add(_TYPE_PRIORITY["money"] * 100 + len(candidate), "money", candidate)

    # 4. Parameter counts
    for token in _extract_params(src):
        _add(_TYPE_PRIORITY["params"] * 100, "params", token)

    # 5. Generic version numbers (if no model-version found)
    if not any(t == "product" for _, t, _ in typed_anchors):
        for m in _GENERIC_VERSION_RE.finditer(src):
            candidate = _clean_match(m.group(0))
            if candidate and len(candidate) >= 2:
                _add(_TYPE_PRIORITY["version"] * 100, "version", candidate)

    # 6. Performance metrics
    for m in _METRIC_RE.finditer(src):
        candidate = _clean_match(m.group(0))
        if candidate:
            _add(_TYPE_PRIORITY["metric"] * 100, "metric", candidate)

    # Sort by priority descending, keep top 5
    typed_anchors.sort(key=lambda x: -x[0])
    top5 = typed_anchors[:5]

    anchors = [v for _, _, v in top5]
    anchors = _dedup_anchors(anchors)

    anchor_types: dict[str, int] = {}
    for _, atype, _ in top5:
        anchor_types[atype] = anchor_types.get(atype, 0) + 1

    has_anchor = len(anchors) > 0

    return {
        "anchors": anchors,
        "anchor_types": anchor_types,
        "has_anchor": has_anchor,
    }


def pick_primary_anchor(anchors: list[str], anchor_types: dict | None = None) -> str | None:
    """Return single best anchor for injection into Q1/Q2.

    Priority: product/model-with-version > benchmark > money > params > version > metric
    Falls back to first anchor if types not provided.
    """
    if not anchors:
        return None

    if not anchor_types:
        return anchors[0]

    # Try each type in priority order
    for atype in ("product", "benchmark", "money", "params", "version", "metric"):
        if anchor_types.get(atype, 0) > 0:
            # Find first anchor of this type by checking which anchor matches patterns
            for a in anchors:
                if atype == "product" and _MODEL_VERSION_RE.search(a):
                    return a
                if atype == "benchmark" and any(
                    kw.lower() in a.lower() for kw in _BENCHMARK_KEYWORDS
                ):
                    return a
                if atype == "money" and _MONEY_RE.search(a):
                    return a
                if atype == "params" and re.search(r"\d+[BM]$", a, re.IGNORECASE):
                    return a
                if atype == "version" and _GENERIC_VERSION_RE.search(a):
                    return a
                if atype == "metric" and _METRIC_RE.search(a):
                    return a

    # Fallback to first anchor
    return anchors[0]


def extract_anchors_from_card(card) -> dict:
    """Convenience: extract anchors from an EduNewsCard (or card-like object).

    Searches: title_plain, what_happened, evidence_lines, fact_check_confirmed,
              technical_interpretation, observation_metrics.
    """
    title = str(getattr(card, "title_plain", "") or "").strip()

    parts: list[str] = []
    for attr in ("what_happened", "technical_interpretation"):
        val = str(getattr(card, attr, "") or "").strip()
        if val:
            parts.append(val)

    for attr in ("evidence_lines", "fact_check_confirmed", "observation_metrics"):
        lst = getattr(card, attr, None) or []
        for item in lst[:3]:
            v = str(item or "").strip()
            if v:
                parts.append(v)

    anchor_text = " ".join(parts)

    source_name = str(getattr(card, "source_name", "") or "").strip()
    published_at = ""
    for attr in ("published_at", "published_at_parsed", "collected_at"):
        val = getattr(card, attr, None)
        if val:
            published_at = str(val).strip()[:10]
            break

    return extract_anchors(
        title=title,
        anchor_text=anchor_text,
        source_name=source_name,
        published_at=published_at,
    )
