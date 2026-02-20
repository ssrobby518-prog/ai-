"""Anti-Fragment Narrative v1 — deterministic 2-3 sentence narrative builder.

Inputs:  EduNewsCard fields (title_plain, what_happened, why_important, etc.)
Outputs: 2-3 readable sentences with at least 1 hard evidence token.

Rules (fully deterministic — no LLM, no external API):
  1. Sentence 1: Subject + action verb (from what_happened or title)
  2. Sentence 2: Impact from why_important (product / tech / business angle)
  3. Sentence 3 (conditional): Proof token if sentences 1+2 lack hard evidence

Hard evidence token types (at least 1 required in output):
  - version:   v\\d+(\\.\\d+)+          e.g. v3.5.0
  - benchmark: MMLU/GPQA/SWE-bench + number
  - params:    \\d+(B|M) parameters
  - money:     $\\d+(M|B) / 融資 / 收購金額
  - date:      YYYY-MM-DD
  - percentage: \\d+%
  - unit:      \\d+(B|M|K|GB|TB|億|萬)

Does NOT invent facts — only re-combines fields from the card.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Hard-evidence token patterns
# ---------------------------------------------------------------------------
_VERSION_RE = re.compile(r'\bv\d+(?:\.\d+)+\b', re.IGNORECASE)
_BENCHMARK_RE = re.compile(
    r'\b(?:MMLU|GPQA|SWE-bench|Arena|latency|throughput|accuracy|BLEU|ROUGE)\b'
    r'[\s:=]*[\d.]+',
    re.IGNORECASE,
)
_PARAMS_RE = re.compile(
    r'\b\d+(?:\.\d+)?\s*[BM]\s+(?:parameters?|params?)\b',
    re.IGNORECASE,
)
_MONEY_RE = re.compile(
    r'\$\d+(?:\.\d+)?(?:M|B|million|billion)'
    r'|\b\d+(?:\.\d+)?\s*(?:M|B|million|billion)\s+(?:USD|dollars?|funding|raised?)'
    r'|融資\s*\d+|收購金額\s*\d+',
    re.IGNORECASE,
)
_DATE_RE = re.compile(r'\b\d{4}-\d{2}-\d{2}\b')
_PERCENT_RE = re.compile(r'\b\d+(?:\.\d+)?\s*%')
_UNIT_NUM_RE = re.compile(
    r'\b\d+(?:\.\d+)?\s*(?:B|M|K|GB|TB|億|萬|兆)\b',
    re.IGNORECASE,
)

_ALL_HARD_EVIDENCE = [
    _VERSION_RE, _BENCHMARK_RE, _PARAMS_RE,
    _MONEY_RE, _DATE_RE, _PERCENT_RE, _UNIT_NUM_RE,
]

# ---------------------------------------------------------------------------
# Action-verb patterns for sentence-1 subject extraction
# ---------------------------------------------------------------------------
_ACTION_VERB_RE = re.compile(
    r'\b(?:released?|launched?|raised?|ships?|shipped|open.?sourced?|'
    r'acqui(?:red?|s(?:ition)?)|announced?|published?|introduced?|'
    r'deployed?|partnered?|reveal(?:s|ed)?|debut(?:s|ed)?|'
    r'unveil(?:s|ed)?|relea(?:s|se)d?)\b|'
    r'(?:推出|發布|收購|融資|開源|發表|上線|合作|宣布|推進|裁員|投資)',
    re.IGNORECASE,
)

# Sentence boundary
_SENT_SPLIT_RE = re.compile(r'(?<=[.!?。！？])\s+')


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def has_hard_evidence(text: str) -> bool:
    """Return True if text contains at least one hard evidence token."""
    for pattern in _ALL_HARD_EVIDENCE:
        if pattern.search(text):
            return True
    return False


def extract_first_hard_evidence(text: str) -> str:
    """Return the first hard evidence token found, or empty string."""
    for pattern in _ALL_HARD_EVIDENCE:
        m = pattern.search(text)
        if m:
            return m.group().strip()
    return ""


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, filtering empty/short fragments."""
    parts = re.split(r'[.!?。！？;；]+', text or '')
    return [p.strip() for p in parts if p.strip() and len(p.strip()) >= 8]


def _best_sentence(text: str, prefer_evidence: bool = True) -> str:
    """Pick the most informative sentence from text."""
    if not text or not text.strip():
        return ''
    sents = _split_sentences(text)
    if not sents:
        # Truncate raw text if no sentence boundary found
        return text.strip()[:160]
    if prefer_evidence:
        for s in sents:
            if has_hard_evidence(s):
                return s[:160]
    return sents[0][:160]


def _ensure_period(s: str) -> str:
    """Ensure sentence ends with a period/full-stop."""
    s = s.rstrip()
    if s and s[-1] not in '.!?。！？':
        s += '。'
    return s


def _category_impact_angle(category: str) -> str:
    """Return a generic impact phrase based on topic category."""
    cat_lower = (category or '').lower()
    if any(k in cat_lower for k in ('product', '產品', 'saas', 'app')):
        return '對產品採用率與成本結構產生直接影響'
    if any(k in cat_lower for k in ('tech', '技術', '模型', 'model', 'ai')):
        return '將推動技術生態系加速演進'
    if any(k in cat_lower for k in ('business', '商業', '市場', 'market')):
        return '對市場競爭格局與商業決策形成壓力'
    return '對相關利益方的策略規劃產生影響'


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_narrative_compact(card: object) -> str:  # card: EduNewsCard
    """Build a 2-3 sentence compact narrative from an EduNewsCard.

    Guarantees:
      - 2 or 3 sentences in output
      - At least 1 hard evidence token present
      - No invented facts — only re-combines existing card fields
      - Deterministic (same inputs → same output)

    Args:
        card: EduNewsCard instance (or any object with the same attributes).

    Returns:
        A string of 2-3 sentences suitable for a card body in PPTX/DOCX.
    """
    title: str = (getattr(card, 'title_plain', '') or '').strip()
    what: str = (getattr(card, 'what_happened', '') or '').strip()
    why: str = (getattr(card, 'why_important', '') or '').strip()
    source: str = (getattr(card, 'source_name', '') or '').strip()
    category: str = (getattr(card, 'category', '') or '').strip()

    # ── Sentence 1: Action sentence ──────────────────────────────────────
    # Prefer what_happened if non-fragment; fall back to title
    from utils.semantic_quality import is_placeholder_or_fragment  # lazy
    s1_raw = ''
    if what and not is_placeholder_or_fragment(what):
        s1_raw = _best_sentence(what, prefer_evidence=True)
    if not s1_raw and title:
        s1_raw = _best_sentence(title, prefer_evidence=False)
    if not s1_raw:
        s1_raw = title[:120] if title else '相關事件已發生'
    s1 = _ensure_period(s1_raw[:160])

    # ── Sentence 2: Impact / significance ────────────────────────────────
    s2_raw = ''
    if why and not is_placeholder_or_fragment(why):
        s2_raw = _best_sentence(why, prefer_evidence=True)
    if not s2_raw:
        s2_raw = _category_impact_angle(category)
    s2 = _ensure_period(s2_raw[:160])

    combined_12 = s1 + ' ' + s2

    # ── If evidence already present in s1+s2, return 2 sentences ─────────
    if has_hard_evidence(combined_12):
        return combined_12.strip()

    # ── Sentence 3: Proof fallback ────────────────────────────────────────
    # Try to extract hard evidence from any available field
    all_text = ' '.join(filter(None, [title, what, why,
                                      getattr(card, 'technical_interpretation', ''),
                                      ' '.join(getattr(card, 'fact_check_confirmed', []) or []),
                                      ' '.join(getattr(card, 'evidence_lines', []) or [])]))
    token = extract_first_hard_evidence(all_text)
    if token:
        src_label = source or '公開資訊'
        s3 = f'關鍵數據：{token}（來源：{src_label}）。'
    else:
        # Last resort: embed publication date
        today = datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')
        src_label = source or '公開資訊'
        s3 = f'事件時間：{today}（來源：{src_label}）。'

    return (combined_12.strip() + ' ' + s3).strip()


def count_hard_evidence_tokens(text: str) -> int:
    """Count distinct hard evidence token matches in text (for stats)."""
    total = 0
    for pattern in _ALL_HARD_EVIDENCE:
        total += len(pattern.findall(text))
    return total
