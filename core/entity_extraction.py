"""Entity extraction pipeline.

Replaces the naive word-split approach with:
- Stopword / junk filtering (EN + ZH)
- Acronym allowlist (< 3 chars but all-caps)
- Title-case sequence detection for English
- URL domain-based org inference
- Alias normalization & deduplication
- TF-IDF-like scoring with title weight > body weight
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Stopword sets
# ---------------------------------------------------------------------------

_EN_STOPWORDS: set[str] = {
    # determiners / articles
    "a",
    "an",
    "the",
    # pronouns
    "i",
    "me",
    "my",
    "myself",
    "we",
    "our",
    "ours",
    "ourselves",
    "you",
    "your",
    "yours",
    "yourself",
    "yourselves",
    "he",
    "him",
    "his",
    "himself",
    "she",
    "her",
    "hers",
    "herself",
    "it",
    "its",
    "itself",
    "they",
    "them",
    "their",
    "theirs",
    "themselves",
    # demonstratives
    "this",
    "that",
    "these",
    "those",
    # common verbs / auxiliaries
    "is",
    "am",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "having",
    "do",
    "does",
    "did",
    "doing",
    "will",
    "would",
    "shall",
    "should",
    "may",
    "might",
    "must",
    "can",
    "could",
    # prepositions / conjunctions / adverbs
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "with",
    "by",
    "from",
    "up",
    "about",
    "into",
    "over",
    "after",
    "out",
    "off",
    "and",
    "but",
    "or",
    "nor",
    "not",
    "no",
    "so",
    "if",
    "then",
    "than",
    "too",
    "very",
    "just",
    "also",
    "now",
    "here",
    "there",
    "when",
    "where",
    "why",
    "how",
    "what",
    "which",
    "who",
    "whom",
    "all",
    "each",
    "every",
    "both",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "only",
    "own",
    "same",
    "as",
    "while",
    "during",
    # common filler / noise from HN-style posts
    "hi",
    "hey",
    "hello",
    "show",
    "ask",
    "tell",
    "hn",
    "hn,",
    "yes",
    "yeah",
    "ok",
    "okay",
    "oh",
    "please",
    "thanks",
    "thank",
    "new",
    "like",
    "use",
    "get",
    "make",
    "made",
    "way",
    "via",
    "still",
    "yet",
    "already",
    "since",
    "before",
    "because",
    "much",
    "well",
    "really",
    "actually",
    "quite",
    "though",
    "however",
    "one",
    "two",
    "three",
    "first",
    "second",
    "third",
    "last",
    "built",
    "using",
    "want",
    "need",
    "think",
    "know",
    "let",
    "see",
    "try",
    "look",
    "find",
    "take",
    "give",
    "work",
    "call",
    "come",
    "go",
    "run",
    "set",
    "put",
    "say",
    "said",
    "told",
    "many",
    "any",
    "simple",
    "enjoy",
    "free",
    "kind",
    "part",
    "lot",
    "bit",
    "even",
    "back",
    "down",
    "through",
    "between",
    "against",
    "under",
    "around",
    "without",
    "within",
    "along",
    "across",
    "per",
    "once",
}

_ZH_STOPWORDS: set[str] = {
    "的",
    "了",
    "在",
    "是",
    "我",
    "有",
    "和",
    "就",
    "不",
    "人",
    "都",
    "一",
    "一個",
    "上",
    "也",
    "很",
    "到",
    "說",
    "要",
    "去",
    "你",
    "會",
    "著",
    "沒有",
    "看",
    "好",
    "自己",
    "這",
    "他",
    "她",
    "它",
    "們",
    "那",
    "些",
    "什麼",
    "吧",
    "但",
    "與",
    "及",
    "或",
    "等",
    "被",
    "把",
    "讓",
    "用",
    "對",
    "為",
    "從",
    "所",
    "而",
    "以",
    "其",
    "中",
    "將",
    "之",
    "更",
    "最",
    "已",
    "能",
    "可以",
    "可",
    "還",
    "再",
}

# All-caps acronyms we always allow (even if < 3 chars)
_KNOWN_ACRONYMS: set[str] = {
    "AI",
    "ML",
    "AR",
    "VR",
    "XR",
    "5G",
    "6G",
    "IoT",
    "EV",
    "US",
    "UK",
    "EU",
    "UN",
    "WHO",
    "FDA",
    "FCC",
    "EPA",
    "SEC",
    "ICE",
    "NSA",
    "CIA",
    "FBI",
    "DOJ",
    "IRS",
    "FTC",
    "DOD",
    "AWS",
    "GCP",
    "API",
    "SDK",
    "GPU",
    "CPU",
    "TPU",
    "NPU",
    "USB",
    "LED",
    "LCD",
    "SSD",
    "RAM",
    "ROM",
    "DNS",
    "TCP",
    "IP",
    "HTTP",
    "LLM",
    "NLP",
    "CV",
    "RL",
    "DL",
    "CEO",
    "CTO",
    "CFO",
    "COO",
    "VP",
    "IPO",
    "M&A",
    "VC",
    "PE",
    "LP",
    "GP",
    "MIT",
    "CMU",
    "ETH",
    "TSMC",
    "ASML",
    "AMD",
    "ARM",
    "IBM",
}

# Known org suffixes for English
_ORG_SUFFIXES: set[str] = {
    "inc",
    "inc.",
    "corp",
    "corp.",
    "co",
    "co.",
    "ltd",
    "ltd.",
    "llc",
    "plc",
    "gmbh",
    "ag",
    "foundation",
    "labs",
    "lab",
    "studio",
    "studios",
    "technologies",
    "technology",
    "tech",
    "ventures",
    "capital",
    "partners",
    "group",
    "holdings",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Entity:
    """A recognized named entity with metadata."""

    text: str  # display form
    normalized: str  # lowercased, alias-resolved
    entity_type: str = "UNKNOWN"  # ORG, PERSON, PRODUCT, PLACE, ACRONYM, OTHER
    count: int = 0
    title_count: int = 0  # appearances in title
    body_count: int = 0  # appearances in body
    score: float = 0.0


@dataclass
class ExtractionResult:
    """Result of entity extraction for a single item."""

    entities: list[Entity] = field(default_factory=list)
    detected_lang: str = ""  # "en", "zh", "mixed"
    top_entity_strings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Alias map for normalization
# ---------------------------------------------------------------------------

_ALIAS_MAP: dict[str, str] = {
    "u.s.": "US",
    "u.s": "US",
    "united states": "US",
    "usa": "US",
    "america": "US",
    "u.k.": "UK",
    "united kingdom": "UK",
    "britain": "UK",
    "e.u.": "EU",
    "european union": "EU",
    "google's": "Google",
    "googles": "Google",
    "apple's": "Apple",
    "apples": "Apple",
    "microsoft's": "Microsoft",
    "microsofts": "Microsoft",
    "amazon's": "Amazon",
    "amazons": "Amazon",
    "meta's": "Meta",
    "metas": "Meta",
    "openai's": "OpenAI",
    "openais": "OpenAI",
}


# ---------------------------------------------------------------------------
# Language detection (lightweight)
# ---------------------------------------------------------------------------

_CJK_RANGES = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff"
    r"\U00020000-\U0002a6df\U0002a700-\U0002ebef]"
)


def detect_language(text: str) -> str:
    """Simple language detection: 'en', 'zh', or 'mixed'."""
    if not text:
        return "en"
    cjk_count = len(_CJK_RANGES.findall(text))
    total = len(text.strip())
    if total == 0:
        return "en"
    ratio = cjk_count / total
    if ratio > 0.3:
        return "zh"
    if ratio > 0.05:
        return "mixed"
    return "en"


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

# Match title-case sequences like "New York Times", "Federal Reserve Board"
_TITLECASE_SEQ = re.compile(
    r"\b([A-Z][a-z]+(?:\s+(?:of|and|the|for|de|van|von|al|el|la|le|du|di)\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b"
)

# Match all-caps tokens (acronyms)
_ALLCAPS_TOKEN = re.compile(r"\b([A-Z][A-Z0-9&]{1,10})\b")

# Match single title-case words (potential proper nouns)
_TITLECASE_WORD = re.compile(r"\b([A-Z][a-z]{2,})\b")

# CJK named entity heuristic: 2-6 char CJK sequences that look like names
# (This is a rough heuristic; proper CJK NER would need a model)
_CJK_ENTITY = re.compile(r"([\u4e00-\u9fff]{2,6})")


def _is_numeric_only(token: str) -> bool:
    """Check if token is purely numeric (digits, dots, commas)."""
    return bool(re.fullmatch(r"[\d.,/%$€¥£]+", token))


def _normalize_token(token: str) -> str:
    """Normalize entity text: strip punctuation edges, resolve aliases."""
    # Strip leading/trailing punctuation
    token = token.strip("'\".,;:!?()[]{}—–-…·")

    # Check alias map (case-insensitive)
    lower = token.lower()
    if lower in _ALIAS_MAP:
        return _ALIAS_MAP[lower]

    # Handle possessives
    if token.endswith("'s") or token.endswith("'s"):
        token = token[:-2]

    return token


def _is_valid_entity(token: str, lang: str) -> bool:
    """Check if a token passes entity validation rules."""
    if not token:
        return False

    normalized_lower = token.lower().strip(".,;:!?'\"()[]")

    # Stopword check
    if normalized_lower in _EN_STOPWORDS:
        return False
    if lang in ("zh", "mixed") and normalized_lower in _ZH_STOPWORDS:
        return False

    # Known acronym → always valid
    if token.upper() in _KNOWN_ACRONYMS:
        return True

    # Length check: < 3 chars only allowed for known acronyms (checked above)
    if len(token) < 3:
        return False

    # Pure numeric
    if _is_numeric_only(token):
        return False

    # URL fragments
    return not (token.startswith("http") or token.startswith("www."))


def _classify_entity_type(token: str) -> str:
    """Rough classification of entity type."""
    upper = token.upper()
    if upper in _KNOWN_ACRONYMS:
        return "ACRONYM"

    # Check for org suffixes in multi-word entities
    words = token.lower().split()
    if any(w in _ORG_SUFFIXES for w in words):
        return "ORG"

    # All-caps → likely acronym or org abbreviation
    if token.isupper() and len(token) >= 2:
        return "ACRONYM"

    # Multi-word title-case → likely ORG or PLACE
    if len(words) > 1 and all(w[0].isupper() or w in ("of", "and", "the", "for") for w in words):
        return "ORG"

    return "OTHER"


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------


def _extract_candidates_en(text: str) -> list[str]:
    """Extract entity candidates from English text."""
    candidates: list[str] = []

    # 1. Title-case sequences (multi-word entities like "New York Times")
    for match in _TITLECASE_SEQ.finditer(text):
        candidates.append(match.group(1))

    # 2. All-caps tokens (acronyms)
    for match in _ALLCAPS_TOKEN.finditer(text):
        token = match.group(1)
        if len(token) >= 2:
            candidates.append(token)

    # 3. Single title-case words (proper nouns)
    for match in _TITLECASE_WORD.finditer(text):
        candidates.append(match.group(1))

    return candidates


def _extract_candidates_zh(text: str) -> list[str]:
    """Extract entity candidates from Chinese text (heuristic)."""
    candidates: list[str] = []
    # CJK sequences of 2-6 chars
    for match in _CJK_ENTITY.finditer(text):
        token = match.group(1)
        if token not in _ZH_STOPWORDS and len(token) >= 2:
            candidates.append(token)
    return candidates


def extract_entities_from_url(url: str) -> list[str]:
    """Extract org name from URL domain."""
    if not url:
        return []
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # Remove www. prefix
        domain = re.sub(r"^www\.", "", domain)
        # Get the primary domain (e.g., "techcrunch" from "techcrunch.com")
        parts = domain.split(".")
        if len(parts) >= 2:
            name = parts[-2]
            # Only return if it's a recognizable name (not generic)
            if len(name) > 3 and name not in ("github", "medium", "substack", "wordpress"):
                return [name.capitalize()]
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_entities(
    title: str,
    body: str,
    url: str = "",
    lang: str = "",
    max_entities: int = 8,
) -> ExtractionResult:
    """Extract, filter, deduplicate, and score entities from a news item.

    Args:
        title: Item title.
        body: Item body text.
        url: Item URL (for domain-based org inference).
        lang: Language hint ('en', 'zh'). Auto-detected if empty.
        max_entities: Maximum entities to return.

    Returns:
        ExtractionResult with scored, deduplicated entities.
    """
    if not lang:
        lang = detect_language(title + " " + body[:500])

    # Collect candidates from title and body separately
    title_candidates: list[str] = []
    body_candidates: list[str] = []

    if lang in ("en", "mixed"):
        title_candidates.extend(_extract_candidates_en(title))
        body_candidates.extend(_extract_candidates_en(body[:2000]))
    if lang in ("zh", "mixed"):
        title_candidates.extend(_extract_candidates_zh(title))
        body_candidates.extend(_extract_candidates_zh(body[:2000]))

    # URL-based entities
    url_entities = extract_entities_from_url(url)
    body_candidates.extend(url_entities)

    # Normalize all candidates
    title_normalized: list[str] = []
    for c in title_candidates:
        n = _normalize_token(c)
        if _is_valid_entity(n, lang):
            title_normalized.append(n)

    body_normalized: list[str] = []
    for c in body_candidates:
        n = _normalize_token(c)
        if _is_valid_entity(n, lang):
            body_normalized.append(n)

    # Count occurrences (case-insensitive dedup key)
    title_counter: Counter[str] = Counter()
    body_counter: Counter[str] = Counter()
    # Map from lowercase key → best display form
    display_forms: dict[str, str] = {}

    for token in title_normalized:
        key = token.lower()
        title_counter[key] += 1
        # Prefer title-case or original form
        if key not in display_forms or (token[0].isupper() and not display_forms[key][0].isupper()):
            display_forms[key] = token

    for token in body_normalized:
        key = token.lower()
        body_counter[key] += 1
        if key not in display_forms or (token[0].isupper() and not display_forms[key][0].isupper()):
            display_forms[key] = token

    # Merge into Entity objects
    all_keys = set(title_counter.keys()) | set(body_counter.keys())
    entities: list[Entity] = []

    for key in all_keys:
        tc = title_counter.get(key, 0)
        bc = body_counter.get(key, 0)
        display = display_forms.get(key, key)
        etype = _classify_entity_type(display)

        # TF-IDF-like scoring: title mentions weighted 3x
        score = tc * 3.0 + bc * 1.0

        entities.append(
            Entity(
                text=display,
                normalized=key,
                entity_type=etype,
                count=tc + bc,
                title_count=tc,
                body_count=bc,
                score=score,
            )
        )

    # Sort by score (descending), then by count
    entities.sort(key=lambda e: (e.score, e.count), reverse=True)

    # Take top N
    top = entities[:max_entities]

    return ExtractionResult(
        entities=top,
        detected_lang=lang,
        top_entity_strings=[e.text for e in top],
    )
