"""Evidence-pack helpers for AI Intel pure-mode pipeline.

Provides:
- AI_KEYWORDS: expanded keyword list for AI relevance detection
- compute_ai_relevance(): keyword scan returning bool
- extract_event_anchors(): deduped anchor tokens from event text
- Per-gate check functions (return (ok, reasons)):
    check_no_boilerplate(), check_q1_structure(),
    check_q2_structure(), check_moves_anchored(),
    check_exec_readability()

All stdlib only — no external dependencies.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# AI keyword list (spec A2 expanded)
# ---------------------------------------------------------------------------

AI_KEYWORDS: list[str] = [
    # Core AI terms
    "AI", "LLM", "GPT", "Claude", "Anthropic", "OpenAI", "Gemini",
    "model", "models", "machine learning", "deep learning",
    "neural", "transformer", "transformers", "diffusion",
    "embedding", "encoder", "inference", "quantization",
    "text-to-image", "multimodal", "agent", "agents",
    "foundation model", "foundation models",
    # Spec A2 additions
    "fine-tune", "fine-tuning", "fine_tune", "fine_tuning",
    "benchmark", "benchmarks", "benchmarking",
    "CUDA", "GPU", "TPU",
    "reasoning", "chain-of-thought",
    "autonomous", "autonomy",
    "synthetic data", "synthetic",
    "hallucin",  # prefix: hallucination/hallucinate
    "Hugging Face", "HuggingFace",
    "RAG", "retrieval-augmented",
    "RLHF", "reinforcement learning",
    "pre-train", "pre-training", "pretrain", "pretraining",
    "tokenizer", "tokenization",
    "vector database", "vector store",
    "prompt", "prompting",
    "parameter", "parameters",
    "compute", "FLOPs",
    "safety", "alignment",
    "Llama", "Mistral", "Falcon", "Stable Diffusion",
    "NVIDIA", "A100", "H100",
]

# Special prefix-only keywords (matched anywhere as substring, no trailing boundary)
_PREFIX_KEYWORDS = {"hallucin"}

# Build a regex from the keywords (longest first to avoid partial matches)
_sorted_kw = sorted(AI_KEYWORDS, key=len, reverse=True)
_kw_patterns = []
for kw in _sorted_kw:
    escaped = re.escape(kw)
    if kw in _PREFIX_KEYWORDS:
        # Prefix match: no trailing word boundary — matches hallucination, hallucinate, etc.
        _kw_patterns.append(r"(?<!\w)" + escaped)
    else:
        _kw_patterns.append(r"(?<!\w)" + escaped + r"(?!\w)")

_AI_RELEVANCE_KW_RE = re.compile(
    "|".join(_kw_patterns),
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Boilerplate regex — 7 banned template phrases (spec D2)
# ---------------------------------------------------------------------------

_BOILERPLATE_RE = re.compile(
    r"最新公告顯示"
    r"|確認.*原文出處"
    r"|原文已提供.*依據"
    r"|避免基於推測"
    r"|引發.*(?:討論|關注|熱議)"
    r"|具有.*(?:實質|重大).*(?:影響|意義)"
    r"|各方.*(?:評估|追蹤).*後續",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_ZH_RE = re.compile(r"[\u4e00-\u9fff]")
_EN_RE = re.compile(r"[a-zA-Z]")

# Anchor extraction patterns
_VERSION_RE = re.compile(r"\b(?:v|version\s*)?(\d+\.\d+(?:\.\d+)?(?:[a-z]\d*)?)\b", re.IGNORECASE)
_NUMBER_PCT_RE = re.compile(r"\b\d+(?:\.\d+)?(?:x|%|B|M|K|bn|mn|trillion|billion|million)?\b")
_COMPANY_RE = re.compile(
    r"\b(?:OpenAI|Anthropic|Google|Microsoft|Meta|NVIDIA|Apple|Amazon|Tesla|DeepMind|"
    r"Hugging Face|HuggingFace|Mistral|Llama|Falcon|Gemini|Claude|GPT|"
    r"xAI|Grok|Perplexity|Cohere|Stability AI|Midjourney|Runway|"
    r"IBM|Intel|AMD|Qualcomm|Samsung|ByteDance|Baidu|Alibaba|Tencent|"
    r"Scale AI|Databricks|Snowflake|MongoDB|Pinecone|Weaviate)\b",
    re.IGNORECASE,
)


def _normalize_ws(text: str) -> str:
    return " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_ai_relevance(
    title: str,
    quote_1: str,
    quote_2: str,
    anchors: list[str] | None = None,
) -> bool:
    """Keyword scan of title + quotes + anchors; returns True if any AI keyword hit."""
    parts = [title or "", quote_1 or "", quote_2 or ""]
    if anchors:
        parts.extend(str(a) for a in anchors if a)
    combined = _normalize_ws(" ".join(parts))
    if not combined:
        return False
    return bool(_AI_RELEVANCE_KW_RE.search(combined))


def extract_event_anchors(
    title: str,
    quote_1: str,
    quote_2: str,
    source_blob: str = "",
    n: int = 8,
) -> list[str]:
    """Extract deduped anchor tokens (company names, numbers, version strings)
    from event text. Returns up to n anchors, deduped and ordered by specificity.
    """
    combined = _normalize_ws(" ".join([title or "", quote_1 or "", quote_2 or "", source_blob or ""]))
    anchors: list[str] = []
    seen: set[str] = set()

    def _add(tok: str) -> None:
        t = tok.strip()
        if t and t.lower() not in seen and len(t) >= 2:
            seen.add(t.lower())
            anchors.append(t)

    # 1. Company names (highest priority)
    for m in _COMPANY_RE.finditer(combined):
        _add(m.group(0))

    # 2. Version strings (e.g. v4.1, GPT-4o)
    for m in _VERSION_RE.finditer(combined):
        _add(m.group(0))

    # 3. Numbers with units / percentages
    for m in _NUMBER_PCT_RE.finditer(combined):
        tok = m.group(0)
        if len(tok) >= 2:
            _add(tok)

    # 4. Title words as fallback (capitalized non-stopwords)
    _STOPWORDS = {"the", "a", "an", "in", "on", "at", "to", "of", "for",
                  "and", "or", "but", "with", "by", "from", "is", "are",
                  "was", "were", "has", "have", "had", "its", "it", "that",
                  "this", "these", "those", "as", "be", "will", "can",
                  "not", "we", "our", "their", "new", "how", "what"}
    for tok in title.split():
        clean_tok = re.sub(r"[^\w\-.]", "", tok)
        if (
            clean_tok
            and clean_tok[0].isupper()
            and clean_tok.lower() not in _STOPWORDS
            and len(clean_tok) >= 3
        ):
            _add(clean_tok)

    return anchors[:n]


def check_no_boilerplate(q1_zh: str, q2_zh: str) -> tuple[bool, list[str]]:
    """Check that q1_zh and q2_zh contain none of the 7 banned template phrases.

    Returns (ok, reasons) where reasons lists the matched banned phrases.
    """
    combined = (q1_zh or "") + " " + (q2_zh or "")
    reasons: list[str] = []
    for m in _BOILERPLATE_RE.finditer(combined):
        phrase = m.group(0)
        reason = f"BOILERPLATE:{phrase[:30]}"
        if reason not in reasons:
            reasons.append(reason)
    ok = len(reasons) == 0
    return (ok, reasons)


def check_q1_structure(
    q1_zh: str,
    actor: str,
    quote_1: str,
    anchors: list[str],
) -> tuple[bool, list[str]]:
    """Q1_STRUCTURE check: q1_zh first sentence contains actor name or anchor,
    and has a traceable verb/object (indicated by 「quote_window」 embedding).

    Returns (ok, reasons).
    """
    reasons: list[str] = []
    q1 = _normalize_ws(q1_zh or "")

    if not q1:
        reasons.append("Q1_EMPTY")
        return (False, reasons)

    # Check CJK char count >= 15 (structure gate — density is checked by ZH narrative gate)
    if len(_ZH_RE.findall(q1)) < 15:
        reasons.append("Q1_CHARS_LOW")

    # Check actor or anchor appears in q1_zh
    actor_n = _normalize_ws(actor or "")
    has_actor = bool(actor_n and actor_n.lower() in q1.lower())
    has_anchor = any(
        a.lower() in q1.lower()
        for a in (anchors or [])
        if len(a) >= 2
    )
    if not has_actor and not has_anchor:
        reasons.append("Q1_NO_ACTOR_OR_ANCHOR")

    # Check quote_window embedding (「...」)
    lq, rq = "\u300c", "\u300d"
    if lq not in q1 or rq not in q1:
        reasons.append("Q1_NO_QUOTE_WINDOW")
    else:
        # Extract embedded window and check it's non-trivial (>= 5 chars)
        m = re.search(lq + r"(.+?)" + rq, q1)
        if not m or len(m.group(1).strip()) < 5:
            reasons.append("Q1_WINDOW_TOO_SHORT")

    return (len(reasons) == 0, reasons)


def check_q2_structure(
    q2_zh: str,
    quote_2: str,
    anchors: list[str],
) -> tuple[bool, list[str]]:
    """Q2_STRUCTURE check: q2_zh contains impact_target from quote_2/anchors
    and has 「quote_window」 embedding.

    Returns (ok, reasons).
    """
    reasons: list[str] = []
    q2 = _normalize_ws(q2_zh or "")

    if not q2:
        reasons.append("Q2_EMPTY")
        return (False, reasons)

    # Check CJK char count >= 15 (structure gate — density checked by ZH narrative gate)
    if len(_ZH_RE.findall(q2)) < 15:
        reasons.append("Q2_CHARS_LOW")

    # Check quote_window embedding
    lq, rq = "\u300c", "\u300d"
    if lq not in q2 or rq not in q2:
        reasons.append("Q2_NO_QUOTE_WINDOW")
    else:
        m = re.search(lq + r"(.+?)" + rq, q2)
        if not m or len(m.group(1).strip()) < 5:
            reasons.append("Q2_WINDOW_TOO_SHORT")

    # Check anchor or quote fragment appears
    has_anchor = any(
        a.lower() in q2.lower()
        for a in (anchors or [])
        if len(a) >= 2
    )
    q2_frag = _normalize_ws(quote_2 or "")[:20].lower()
    has_quote_frag = bool(q2_frag and q2_frag in q2.lower())
    if not has_anchor and not has_quote_frag:
        reasons.append("Q2_NO_ANCHOR_OR_QUOTE_FRAG")

    return (len(reasons) == 0, reasons)


def check_moves_anchored(
    moves: list[str],
    risks: list[str],
    anchors: list[str],
) -> tuple[bool, list[str]]:
    """MOVES_ANCHORED check: each move and risk bullet contains >= 1 anchor token.

    Returns (ok, reasons) listing unanchored bullets.
    """
    reasons: list[str] = []
    all_bullets = list(moves or []) + list(risks or [])
    anchor_list = [a for a in (anchors or []) if len(a) >= 2]

    if not anchor_list:
        # No anchors available — cannot enforce; pass with note
        return (True, [])

    for bullet in all_bullets:
        b_lower = (bullet or "").lower()
        has_anchor = any(a.lower() in b_lower for a in anchor_list)
        if not has_anchor:
            short = (bullet or "")[:40]
            reasons.append(f"UNANCHORED_BULLET:{short}")

    return (len(reasons) == 0, reasons)


def check_exec_readability(
    q1_zh: str,
    q2_zh: str,
    actor: str,
    quote_window_1: str,
    quote_window_2: str,
) -> tuple[bool, list[str]]:
    """EXEC_PRODUCT_READABILITY check:
    - No boilerplate in q1/q2
    - Both quote windows embedded
    - Minimum ZH char density
    - No excessive English ratio
    - Actor name traceable

    Returns (ok, reasons).
    """
    reasons: list[str] = []
    q1 = _normalize_ws(q1_zh or "")
    q2 = _normalize_ws(q2_zh or "")
    lq, rq = "\u300c", "\u300d"

    # Boilerplate check
    bp_ok, bp_reasons = check_no_boilerplate(q1, q2)
    if not bp_ok:
        reasons.extend(bp_reasons)

    # Quote windows embedded
    qw1 = _normalize_ws(quote_window_1 or "")
    qw2 = _normalize_ws(quote_window_2 or "")
    if qw1 and (lq + qw1 + rq) not in q1:
        reasons.append("Q1_WINDOW_MISSING")
    if qw2 and (lq + qw2 + rq) not in q2:
        reasons.append("Q2_WINDOW_MISSING")

    # ZH char density (readability gate — lenient threshold; density enforced by ZH narrative gate)
    for label, text in [("Q1", q1), ("Q2", q2)]:
        if text:
            zh_count = len(_ZH_RE.findall(text))
            en_count = len(_EN_RE.findall(text))
            total = len(text)
            if zh_count < 15:
                reasons.append(f"{label}_ZH_LOW:{zh_count}")
            if total > 0 and en_count / total > 0.70:
                reasons.append(f"{label}_EN_RATIO_HIGH")

    # Actor traceable in q1
    actor_n = _normalize_ws(actor or "")
    if actor_n and actor_n not in q1:
        reasons.append("Q1_ACTOR_MISSING")

    return (len(reasons) == 0, reasons)
