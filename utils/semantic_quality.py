"""Semantic quality utilities — meaning-bearing content detection.

Distinguishes genuine information from "air":
  - is_placeholder_or_fragment(): catches empty, template remnants, broken phrases
  - semantic_density_score(): 0-100 score for evidence-backed content
  - count_evidence_terms() / count_evidence_numbers() / count_sentences(): evidence counters

Used by core/content_strategy.semantic_guard_text() to backfill hollow content
before it reaches PPT / DOCX rendering.  Never modifies schemas.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Sentence boundary
# ---------------------------------------------------------------------------
_SENTENCE_END_RE = re.compile(r"[.!?。！？;；]")

# ---------------------------------------------------------------------------
# Placeholder / fragment patterns
# ---------------------------------------------------------------------------

# Lone sequence number ("2.", "3)") or lone bullet
_LONE_BULLET_RE = re.compile(r"^\s*([0-9]+[.)]\s*|[•\-—*→]\s*)$")

# Known template remnants that survive sanitize()
_TEMPLATE_REMNANT_RE = re.compile(
    r"Last\s+\w+\s+was\b"     # "Last July was…"
    r"|解決方\s*[記表]"        # truncation artifact "解決方 記"
    r"|WHY IT MATTERS:\s*$"   # unclosed template tag
    r"|^的趨勢",               # dangling Chinese phrase
    re.IGNORECASE,
)

# Starts with Chinese particle/connector (function word, not content)
_ZH_PARTICLE_START_RE = re.compile(
    r"^[的了而且因此以及與及或在也就是並且由於通過所以但是然而況且]"
)

# Trailing English connector or comma
_TRAILING_CONNECTOR_RE = re.compile(
    r"\b(?:but|as|and|or|to|of|for|with|in|on|at|by|from|that|this)[,，]?\s*$"
    r"|[,，]\s*$",
    re.IGNORECASE,
)

# Chinese trailing particles (last character only)
_ZH_TRAILING = frozenset("的了而與來記是在和或及對從向把被讓給以也就因此所以但是然而況且")

# ---------------------------------------------------------------------------
# Evidence term patterns
# ---------------------------------------------------------------------------

# AI domain keywords (case-insensitive)
_DOMAIN_TERM_RE = re.compile(
    r"\b(?:AI|LLM|GPU|model|agent|launch|release|acquire|fund|partner|"
    r"inference|training|deploy|benchmark|patent|regulation|vulnerability|"
    r"API|SDK|SaaS|token|parameter|context|fine.?tune|RLHF|RAG|vector|"
    r"transformer|diffusion|multimodal|copilot|chatbot|CEO|CTO|IPO|M&A)\b|"
    r"(?:人工智慧|大模型|算力|推理|訓練|部署|漏洞|收購|發布|推出|合作|"
    r"開源|專利|監管|裁員|融資|晶片|雲端|資安|算法|架構|平台)",
    re.IGNORECASE,
)

# English proper-noun pattern (Title-case or ALL-CAPS, ≥ 2 chars)
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-zA-Z]{1,}\b")

# English stopwords to exclude from proper-noun counting
_STOPWORDS_EN = frozenset([
    "the", "is", "was", "are", "were", "be", "been", "being",
    "a", "an", "of", "in", "on", "at", "to", "for", "with", "by", "from",
    "and", "or", "but", "as", "that", "this", "it", "he", "she", "they",
    "we", "you", "not", "no", "so", "if", "do", "did", "has", "have",
    "had", "can", "could", "would", "should", "will", "may", "might",
    "must", "shall", "than", "then", "its", "our", "your", "their",
    "into", "onto", "out", "up", "down", "off", "over", "under",
    "between", "through", "before", "after", "since", "until", "about",
    "also", "more", "some", "any", "all", "each", "both", "first", "new",
    "other", "most", "now", "just", "like", "when", "how", "what", "who",
    "which", "where", "why", "per", "across", "without", "within",
    "said", "says", "its", "their", "has", "had", "have",
    "This", "That", "The", "In", "At", "For", "With", "By", "From",
    "And", "Or", "But", "As", "If", "So", "No", "Not", "We", "He", "She",
    "It", "They", "Our", "Also", "More", "Some", "All", "Each", "Most",
    "Now", "Just", "When", "How", "What", "Who", "Which", "Where", "Why",
])

# ---------------------------------------------------------------------------
# Evidence number pattern
# ---------------------------------------------------------------------------
_EVIDENCE_NUM_RE = re.compile(
    r"\$[\d,.]+[BMKbmk]?"                                # dollar: $30k, $1.5B
    r"|£[\d,.]+[BMKbmk]?"                                # pound: £500M
    r"|\d+%"                                              # percentage: 50%
    r"|\bv?\d+\.\d+"                                     # version: v1.2, 3.5
    r"|\d+[\s]*(billion|million|thousand|M|B|K|GB|TB|萬|億|兆|天|週|月)"  # 1M, 1 million
    r"|\d+[BMKbmk]\b"                                    # compact: 1M, 5B, 10k
    r"|\d+/\d+"                                          # ratio: 3/5, impact=4/5
    r"|\d{4}",                                           # 4-digit year
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def count_evidence_terms(text: str) -> int:
    """Count verifiable proper nouns and AI domain terms.

    Counts (deduplicated):
    - AI domain keywords (AI, LLM, GPU, model, RAG, etc.)
    - Chinese domain terms (人工智慧, 大模型, etc.)
    - English proper nouns (Title-case ≥ 2 chars, excl. stopwords)
    """
    if not text:
        return 0
    seen: set[str] = set()

    for m in _DOMAIN_TERM_RE.finditer(text):
        seen.add(m.group().lower())

    for m in _PROPER_NOUN_RE.finditer(text):
        token = m.group()
        if token not in _STOPWORDS_EN:
            seen.add(token.lower())

    return len(seen)


def count_evidence_numbers(text: str) -> int:
    """Count numeric evidence markers ($X, N%, vN.M, ratios, years, units)."""
    if not text:
        return 0
    return len(_EVIDENCE_NUM_RE.findall(text))


def count_sentences(text: str) -> int:
    """Count sentences by sentence-ending punctuation. Minimum 1 if non-empty."""
    if not text or not text.strip():
        return 0
    count = len(_SENTENCE_END_RE.findall(text))
    return max(count, 1)


def is_placeholder_or_fragment(text: str) -> bool:
    """Return True if text is hollow — empty, template remnant, or broken fragment.

    Detected cases:
    - empty / whitespace-only
    - lone sequence number ("2.", "3)") or lone bullet ("•", "—")
    - known template remnants ("Last July was", "解決方 記", "WHY IT MATTERS:")
    - starts with CJK particle/connector AND < 40 chars AND no sentence end
    - ends with CJK trailing particle AND no sentence boundary
    - trailing English connector ("but," "and" "or,")
    - very short (< 8 non-space chars) AND no entity or number
    """
    if not text:
        return True
    s = text.strip()
    if not s:
        return True

    # Lone bullet / sequence number
    if _LONE_BULLET_RE.match(s):
        return True

    # Template remnants
    if _TEMPLATE_REMNANT_RE.search(s):
        return True

    # Very short text with no entity or number
    nc = s.replace(" ", "")
    if len(nc) < 8:
        has_num = bool(re.search(r"\d", s))
        has_entity = bool(re.search(r"[A-Z][a-zA-Z]+|[\u4e00-\u9fff]{2,}", s))
        if not has_num and not has_entity:
            return True

    # Medium-length checks (< 40 chars, no sentence end)
    if len(s) < 40:
        has_sentence_end = bool(_SENTENCE_END_RE.search(s))
        if not has_sentence_end:
            # Starts with ZH particle/connector
            if _ZH_PARTICLE_START_RE.match(s):
                return True
            # Ends with ZH trailing particle
            if s and s[-1] in _ZH_TRAILING:
                return True
            # Trailing English connector
            if _TRAILING_CONNECTOR_RE.search(s):
                return True

    return False


def semantic_density_score(text: str) -> int:
    """Compute semantic density score 0-100.

    Scoring breakdown:
    - is_placeholder_or_fragment → 0 (hard floor)
    - Very short text (< 60 non-space chars) → capped at 40
    - terms_score : ≥2 terms → 30, ≥1 → 15, else 0
    - numbers_score: ≥1 number → 30, else 0
    - sentences_score: ≥1 sentence → 40, else 0
    Max = 100 (30 + 30 + 40)
    """
    s = (text or "").strip()
    if not s or is_placeholder_or_fragment(s):
        return 0

    char_count = len(s.replace(" ", ""))

    terms = count_evidence_terms(s)
    numbers = count_evidence_numbers(s)
    sentences = count_sentences(s)

    terms_score = 30 if terms >= 2 else (15 if terms >= 1 else 0)
    numbers_score = 30 if numbers >= 1 else 0
    sentences_score = 40 if sentences >= 1 else 0

    score = terms_score + numbers_score + sentences_score

    # Cap very short text — prevents single-term snippets from scoring high
    if char_count < 40:
        score = min(score, 40)

    return min(100, score)
