"""Bullet Normalizer — Anti-Fragment Narrative v1.

Enforces minimum bullet length (12 chars) and removes forbidden fragments.
All bullet lists destined for PPTX / DOCX must pass through this module.

Rules:
  - Any bullet < 12 chars is merged with the next one using '；'
  - Forbidden patterns are dropped before merge step
  - Final list contains only bullets >= 12 chars
  - Preserves original meaning — no paraphrasing or invented content

Forbidden patterns (subset of banned-phrase list):
  - "的趨勢…" — dangling trend phrase
  - "解決方記" / "解決方表" — truncation artifacts
  - Lone sequence number "2." / "3)"
  - "Last July was…" / "Last … was" template remnants
  - "Desktop smoke signal"
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Forbidden fragment patterns (pre-merge filter)
# ---------------------------------------------------------------------------
_FORBIDDEN_PATTERNS: list[str] = [
    r'^\s*的趨勢[…\.。]+',         # dangling trend phrase
    r'^\s*解決方\s*[記表]?',        # truncation artifact
    r'^\s*\d+[.)]\s*$',             # lone sequence number "2."
    r'Last\s+\w+\s+was\b',          # "Last July was…"
    r'Desktop\s+smoke\s+signal',     # smoke-signal placeholder
    r'^\s*[•\-—*→]\s*$',            # lone bullet character
]
_FORBIDDEN_RE = re.compile('|'.join(_FORBIDDEN_PATTERNS), re.IGNORECASE)

# Banned phrases from the broader content system
_BANNED_PHRASES: list[str] = [
    '的趨勢', '解決方 記', 'Last July was',
    'Desktop smoke signal', 'signals_insufficient=true',
    '低信心事件候選',
]


def _is_forbidden(bullet: str) -> bool:
    """Return True if bullet matches a forbidden pattern."""
    s = bullet.strip()
    if not s:
        return True
    if _FORBIDDEN_RE.search(s):
        return True
    for phrase in _BANNED_PHRASES:
        if phrase in s:
            return True
    return False


def normalize_bullets(
    bullets: list[str],
    min_len: int = 12,
) -> list[str]:
    """Normalize bullet list to enforce minimum character length.

    Steps:
      1. Remove forbidden / fragment bullets
      2. Merge consecutive bullets where the first is < min_len chars
         using '；' as joiner
      3. Return only bullets with len >= min_len

    Args:
        bullets:  Input list of bullet strings.
        min_len:  Minimum acceptable character count (default 12).

    Returns:
        Cleaned list of bullets, all >= min_len chars.
    """
    from utils.semantic_quality import is_placeholder_or_fragment  # lazy

    if not bullets:
        return []

    # Step 1 — filter forbidden / empty / placeholder items
    cleaned: list[str] = []
    for b in bullets:
        b = b.strip()
        if not b:
            continue
        if _is_forbidden(b):
            continue
        if is_placeholder_or_fragment(b):
            continue
        cleaned.append(b)

    if not cleaned:
        return []

    # Step 2 — merge bullets shorter than min_len with the next
    result: list[str] = []
    i = 0
    while i < len(cleaned):
        current = cleaned[i]
        if len(current) < min_len and (i + 1) < len(cleaned):
            merged = current + '；' + cleaned[i + 1]
            result.append(merged)
            i += 2
        else:
            result.append(current)
            i += 1

    # Step 3 — final gate: drop anything still < min_len
    return [b for b in result if len(b) >= min_len]


def normalize_bullets_safe(
    bullets: list[str],
    min_len: int = 12,
    fallback: str = '持續監控此事件後續發展（T+7）。',
) -> list[str]:
    """Same as normalize_bullets but guarantees at least one bullet.

    If the result would be empty, returns [fallback].
    """
    result = normalize_bullets(bullets, min_len=min_len)
    if not result:
        return [fallback] if len(fallback) >= min_len else ['持續監控此事件後續發展（T+7）。']
    return result


def compute_bullet_stats(bullet_lists: list[list[str]]) -> dict:
    """Compute aggregate bullet length statistics for meta JSON.

    Args:
        bullet_lists: A list-of-lists, each inner list is one card's bullets.

    Returns:
        Dict with avg_bullet_len, min_bullet_len (across all bullets).
    """
    all_lengths: list[int] = []
    for bl in bullet_lists:
        for b in bl:
            if b.strip():
                all_lengths.append(len(b.strip()))
    if not all_lengths:
        return {'avg_bullet_len': 0, 'min_bullet_len': 0}
    return {
        'avg_bullet_len': round(sum(all_lengths) / len(all_lengths), 1),
        'min_bullet_len': min(all_lengths),
    }
