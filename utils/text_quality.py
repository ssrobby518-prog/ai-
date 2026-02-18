"""Text quality utilities — fragment detection, trailing-fragment trimming, density checks.

Used by content_strategy to sanitize all text bound for PPT/DOCX output.
ReportQualityGuard uses density helpers to verify per-block information density.
"""

from __future__ import annotations

import re

# High-risk trailing words that indicate a sentence was cut mid-thought.
_TRAILING_ZH = ("的", "了", "而", "與", "來", "記", "是", "在", "和", "或", "及", "對", "從", "向", "把", "被", "讓", "給")
_TRAILING_EN_RE = re.compile(
    r"\b(?:to|and|or|by|the|a|an|of|in|on|at|for|with|is|was|are|were|that|this|it|but|as)\s*$",
    re.IGNORECASE,
)
# Comma / connector endings (e.g. "announced a deal,")
_TRAILING_CONNECTOR_RE = re.compile(
    r"[,，、]\s*$",
)

# Sentence boundary characters.
_SENTENCE_END_RE = re.compile(r"[.!?。！？;；]")

# Evidence patterns: numbers, URLs, proper nouns.
_HAS_NUMBER_RE = re.compile(r"\d")
_HAS_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_HAS_ENTITY_RE = re.compile(r"[A-Z][a-z]{2,}|[\u4e00-\u9fff]{2,}")

# Evidence terms used for density checking (AI domain terms).
_EVIDENCE_TERM_RE = re.compile(
    r"\b(?:AI|LLM|GPU|model|agent|launch|release|acquire|fund|partner|"
    r"inference|training|deploy|benchmark|patent|regulation|vulnerability|"
    r"API|SDK|SaaS|token|parameter|context|fine.?tune|RLHF|RAG|vector|"
    r"transformer|diffusion|multimodal|copilot|chatbot)\b|"
    r"(?:人工智慧|大模型|算力|推理|訓練|部署|漏洞|收購|發布|推出|合作|"
    r"開源|專利|監管|裁員|融資)",
    re.IGNORECASE,
)

# Number patterns: dollar amounts, percentages, versions, counts, ratios (e.g. 4/5).
_EVIDENCE_NUM_RE = re.compile(
    r"\$[\d,.]+[BMKbmk]?|\d+%|\bv?\d+\.\d+|"
    r"\d{2,}[\s]*(billion|million|thousand|M|B|K|GB|TB|萬|億|兆)|"
    r"\d+/\d+",
    re.IGNORECASE,
)


def trim_trailing_fragment(text: str) -> str:
    """If the text ends on a high-risk trailing word, trim back to the previous sentence boundary."""
    if not text or not text.strip():
        return ""
    s = text.strip()

    # Check Chinese trailing particles.
    if s and s[-1] in _TRAILING_ZH:
        # Find previous sentence boundary.
        m = list(_SENTENCE_END_RE.finditer(s[:-1]))
        if m:
            return s[: m[-1].end()].strip()
        # No sentence boundary found — return as-is rather than destroying everything.
        return s

    # Check English trailing words.
    if _TRAILING_EN_RE.search(s):
        m = list(_SENTENCE_END_RE.finditer(s))
        if m:
            return s[: m[-1].end()].strip()
        return s

    # Check trailing commas/connectors.
    if _TRAILING_CONNECTOR_RE.search(s):
        m = list(_SENTENCE_END_RE.finditer(s))
        if m:
            return s[: m[-1].end()].strip()
        # Strip trailing comma at minimum.
        return s.rstrip(",，、 ")

    return s


def is_fragment(text: str) -> bool:
    """Return True if text is an isolated fragment without substance.

    Expanded (v5.5): also flags English trailing words and Chinese particles
    in strings under 40 chars that lack a sentence-ending punctuation.
    """
    if not text:
        return True
    s = text.strip()

    # Short fragment (< 12 chars): only pass if it has a number, URL, or entity.
    if len(s) < 12:
        if _HAS_NUMBER_RE.search(s):
            return False
        if _HAS_URL_RE.search(s):
            return False
        # String starting with a Chinese particle is a fragment even if it
        # contains 2+ CJK chars that would otherwise look like an entity.
        if s and s[0] in _TRAILING_ZH:
            return True
        if _HAS_ENTITY_RE.search(s):
            return False
        return True

    # Medium fragment (< 40 chars): check for trailing words/particles without sentence ending.
    if len(s) < 40:
        has_sentence_end = bool(_SENTENCE_END_RE.search(s))
        if not has_sentence_end:
            if _TRAILING_EN_RE.search(s):
                return True
            if s[-1] in _TRAILING_ZH:
                return True

    return False


# ---------------------------------------------------------------------------
# Per-block density helpers (used by ReportQualityGuard)
# ---------------------------------------------------------------------------

def count_evidence_terms(text: str) -> int:
    """Count AI-domain evidence terms in text."""
    return len(_EVIDENCE_TERM_RE.findall(text))


def count_evidence_numbers(text: str) -> int:
    """Count numeric evidence markers ($X, N%, vN.M, etc.)."""
    return len(_EVIDENCE_NUM_RE.findall(text))


def count_sentences(text: str) -> int:
    """Count sentences by sentence-ending punctuation."""
    if not text or not text.strip():
        return 0
    # Count sentence boundaries; minimum 1 if text is non-empty.
    count = len(_SENTENCE_END_RE.findall(text))
    return max(count, 1) if text.strip() else 0
