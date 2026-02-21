"""utils/narrative_compactor_v2.py — Narrative Compactor v2.

Stdlib-only. No new pip deps.

Converts "sentence piles / repetitive template fragments" into a clean
2–3 sentence narrative (Traditional Chinese focus) per event card.

Public API:
    build_narrative_v2(card) -> dict
        narrative_2to3_sentences_zh  str   — 2–3 complete sentences, ZH-dominant
        bullets_2to3                 list  — 2–3 bullets (each >= 12 chars)
        proof_line                   str   — "證據：來源：{src}（YYYY-MM-DD）"
        debug_stats                  dict  — {dedup_ratio, sentences_used, zh_ratio}
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # No schema import needed at runtime; we use getattr throughout

# ---------------------------------------------------------------------------
# Sentence splitter — handles ZH/EN mixed text
# ---------------------------------------------------------------------------

_SENT_SPLIT_RE = re.compile(
    r"(?<=[。！？；])"          # After CJK terminators (no space needed)
    r"|(?<=[.?!])\s+"          # After EN terminators, followed by space
    r"|\n+"                    # Newlines
)

_ISO_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

# ---------------------------------------------------------------------------
# Keyword patterns for sentence classification
# ---------------------------------------------------------------------------

_EVENT_RE = re.compile(
    r"release|launch|rais|acqui|updat|ship|benchmark|deploy|announc|partner|integrat|"
    r"introduc|publish|open.sourc|fund|sign|complet|enabl|creat|generat|achieve|reach|"
    r"發布|推出|融資|收購|更新|發表|上線|部署|宣布|合作|整合|開源|發行|上市|"
    r"採用|達到|突破|實現|簽署|完成|建立|推進|提升|改善|優化",
    re.IGNORECASE,
)

_IMPACT_RE = re.compile(
    r"\bcost\b|\bperf\b|market|risk|patent|copyright|competi|efficien|secur|regulat|"
    r"policy|workforce|productiv|revenue|saving|benchmark|outperform|challeng|"
    r"政策|版權|競爭|效能|成本|市場|風險|影響|優勢|挑戰|安全|監管|法規|"
    r"生產力|收益|節省|超越|超越|外洩|依賴|壓縮|加速|主導",
    re.IGNORECASE,
)

_NEXT_RE = re.compile(
    r"\bwill\b|\bplan\b|\bnext\b|rollout|roadmap|schedul|expect|target|aim|"
    r"預計|下一步|計畫|規劃|接下來|未來|預期|即將|下月|下季|年底|正在|準備",
    re.IGNORECASE,
)

# Banned substrings / internal tags — kept minimal; rely on exec_sanitizer if available
_BANNED_SUBSTRINGS: list[str] = [
    "的趨勢，解決方 記",
    "詳見原始來源",
    "監控中 本欄暫無事件",
    "Evidence summary: sources=",
    "Key terms: ",
    "validate source evidence and related numbers",
    "run small-scope checks against current workflow",
    "escalate only if next scan confirms sustained",
]

_INTERNAL_TAG_RE = re.compile(
    r"^(WATCH|TEST|MOVE|FIX|TODO|NOTE)\b\s*[：:]\s*",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clean_raw(text: str) -> str:
    """Strip internal tags and banned fragments from a raw string."""
    text = _INTERNAL_TAG_RE.sub("", text.strip())
    for banned in _BANNED_SUBSTRINGS:
        text = text.replace(banned, "")
    return text.strip()


def _split_sentences(text: str) -> list[str]:
    """Split ZH/EN mixed text into sentence-like chunks (>= 4 chars)."""
    parts = _SENT_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) >= 4]


def _zh_char_count(text: str) -> int:
    return sum(1 for c in text if "\u4e00" <= c <= "\u9fff")


def _ascii_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for c in text if ord(c) < 128) / len(text)


def _near_dedup(
    sents: list[str],
    threshold: float = 0.86,
) -> tuple[list[str], float]:
    """Remove near-duplicate sentences. Returns (kept, dedup_ratio)."""
    if not sents:
        return [], 0.0
    kept: list[str] = []
    for s in sents:
        if not any(
            SequenceMatcher(None, s, k).ratio() >= threshold for k in kept
        ):
            kept.append(s)
    original = len(sents)
    dedup_ratio = round(1.0 - len(kept) / original, 3) if original else 0.0
    return kept, dedup_ratio


def _classify(sents: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Return (event_sents, impact_sents, next_sents)."""
    event: list[str] = []
    impact: list[str] = []
    nxt: list[str] = []
    for s in sents:
        if _EVENT_RE.search(s):
            event.append(s)
        if _IMPACT_RE.search(s):
            impact.append(s)
        if _NEXT_RE.search(s):
            nxt.append(s)
    return event, impact, nxt


def _select_2to3(
    event: list[str],
    impact: list[str],
    nxt: list[str],
    all_sents: list[str],
) -> list[str]:
    """Select 2–3 sentences: at least event + impact; add next if available."""
    selected: list[str] = []
    used: set[str] = set()

    # 1) Event sentence first
    for s in event:
        if s not in used:
            selected.append(s)
            used.add(s)
            break

    # 2) Impact sentence
    for s in impact:
        if s not in used:
            selected.append(s)
            used.add(s)
            break

    # 3) Next sentence (optional 3rd)
    for s in nxt:
        if s not in used and len(selected) < 3:
            selected.append(s)
            used.add(s)
            break

    # Fill to minimum 2 from all_sents if needed
    for s in all_sents:
        if len(selected) >= 2:
            break
        if s not in used:
            selected.append(s)
            used.add(s)

    return selected[:3]


def _ensure_terminated(text: str) -> str:
    """Ensure text ends with a sentence terminator."""
    t = text.strip()
    if t and t[-1] not in "。！？；.!?":
        t += "。"
    return t


def _apply_zh_skeleton(card, original_sents: list[str]) -> str:
    """When selected sentences are too English-heavy, rebuild from card ZH fields.

    Uses existing card attributes only — no new facts introduced.
    """
    what = str(getattr(card, "what_happened", "") or "").strip()
    tech = str(getattr(card, "technical_interpretation", "") or "").strip()
    why = str(getattr(card, "why_important", "") or "").strip()
    title = str(getattr(card, "title_plain", "") or "").strip()

    parts: list[str] = []

    # Event base from ZH fields
    event_base = what or tech or title
    if event_base:
        s = event_base[:180]
        parts.append(s if s[-1:] in "。！？；" else s + "。")

    # Impact base
    if why:
        s = why[:180]
        parts.append(s if s[-1:] in "。！？；" else s + "。")

    if not parts:
        # Last resort: keep original sentences as-is
        return "".join(original_sents[:2])

    return "".join(parts)


def _build_anchor_text(card) -> str:
    """Build a rich anchor text from all relevant card fields."""
    parts: list[str] = []

    for attr in ("what_happened", "technical_interpretation", "why_important"):
        val = str(getattr(card, attr, "") or "").strip()
        if val:
            parts.append(val)

    for attr in ("evidence_lines", "fact_check_confirmed"):
        items = getattr(card, attr, None) or []
        for item in items[:3]:
            val = str(item or "").strip()
            if val and len(val) >= 8:
                parts.append(val)

    combined = " ".join(parts)
    if len(combined) < 20:
        title = str(getattr(card, "title_plain", "") or "").strip()
        combined = (title + " " + combined).strip()

    return combined


def _make_proof_line(card) -> str:
    """Build proof line with ISO date: '證據：來源：{src}（YYYY-MM-DD）'."""
    try:
        from utils.longform_narrative import _make_date_proof_line
        return _make_date_proof_line(card)
    except Exception:
        pass

    source = (getattr(card, "source_name", "") or "未知來源").strip()
    for attr in ("published_at", "published_at_parsed", "collected_at"):
        val = getattr(card, attr, None)
        if val:
            m = _ISO_DATE_RE.search(str(val))
            if m:
                return f"證據：來源：{source}（{m.group(1)}）"
    today = datetime.now(timezone.utc).date().isoformat()
    return f"證據：來源：{source}（{today}）"


def _build_bullets(card) -> list[str]:
    """Build 2–3 bullets from card fields; each bullet >= 12 chars."""
    candidates: list[str] = []

    for attr in ("evidence_lines", "fact_check_confirmed", "derivable_effects", "action_items"):
        items = getattr(card, attr, None) or []
        for item in items:
            s = _clean_raw(str(item or "").strip())
            if len(s) >= 12:
                candidates.append(s)

    # Sanitize via exec_sanitizer if available
    try:
        from utils.exec_sanitizer import sanitize_exec_text
        candidates = [sanitize_exec_text(c) for c in candidates]
    except Exception:
        pass

    # Near-dedup within candidates
    kept, _ = _near_dedup(candidates, threshold=0.80)

    # If still fewer than 2, fall back to split card ZH fields
    if len(kept) < 2:
        for field in ("why_important", "technical_interpretation", "what_happened"):
            val = str(getattr(card, field, "") or "").strip()
            if len(val) >= 12:
                for s in _split_sentences(val)[:2]:
                    s = _clean_raw(s)
                    if len(s) >= 12 and not any(
                        SequenceMatcher(None, s, k).ratio() >= 0.80 for k in kept
                    ):
                        kept.append(s)
                    if len(kept) >= 3:
                        break
            if len(kept) >= 3:
                break

    return kept[:3]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_narrative_v2(card) -> dict:
    """Build 2–3 sentence clean narrative from card fields.

    Returns:
        {
            "narrative_2to3_sentences_zh": str,
            "bullets_2to3":               list[str],
            "proof_line":                 str,
            "debug_stats": {
                "dedup_ratio":     float,
                "sentences_used":  int,
                "zh_ratio":        float,
            }
        }
    """
    # 1. Anchor text
    anchor = _build_anchor_text(card)

    # 2. Split into sentences & clean each one
    raw_sents = [_clean_raw(s) for s in _split_sentences(anchor)]
    raw_sents = [s for s in raw_sents if len(s) >= 8]

    # 3. Near-dedup
    deduped, dedup_ratio = _near_dedup(raw_sents)

    # 4. Classify
    event, impact, nxt = _classify(deduped)

    # 5. Select 2–3
    selected = _select_2to3(event, impact, nxt, deduped)

    # 6. ZH ratio check — apply skeleton if too English-heavy
    combined = " ".join(selected)
    zh_chars = _zh_char_count(combined)
    asc_r = _ascii_ratio(combined)

    if zh_chars < 12 and asc_r > 0.60:
        combined = _apply_zh_skeleton(card, selected)
        # Re-split selected for sentence count tracking
        selected = _split_sentences(combined) or selected

    # 7. Ensure termination
    narrative = _ensure_terminated(combined)

    # 8. Final ZH ratio
    final_zh = _zh_char_count(narrative)
    zh_ratio = round(final_zh / max(1, len(narrative)), 3)

    # 9. Proof line
    proof_line = _make_proof_line(card)

    # 10. Bullets
    bullets = _build_bullets(card)

    return {
        "narrative_2to3_sentences_zh": narrative,
        "bullets_2to3": bullets,
        "proof_line": proof_line,
        "debug_stats": {
            "dedup_ratio": dedup_ratio,
            "sentences_used": min(len(selected), 3),
            "zh_ratio": zh_ratio,
        },
    }
