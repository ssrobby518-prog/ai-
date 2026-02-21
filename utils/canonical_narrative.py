"""utils/canonical_narrative.py — Canonical Payload v3 (Iteration 2).

Stdlib-only.  No new pip dependencies.

Single source of truth for ALL text exits:
  PPT / DOCX / Matrix / Watchlist / Overview / Ranking / Pending

Public API
----------
    build_canonical_payload(card) -> dict
    get_canonical_payload(card)   -> dict   # cached via card._canonical_payload_v3

Canonical fields (fixed schema):
    q1_event_2sent_zh     str        2 sentences: what happened (ZH body)
    q2_impact_2sent_zh    str        2 sentences: why important (ZH body)
    q3_moves_3bullets_zh  list[str]  3 bullets >= 12 chars (ZH body)
    risks_2bullets_zh     list[str]  2 bullets (ZH body)
    proof_line            str        證據：來源：X（YYYY-MM-DD）
    title_clean           str        clean title (no tags / banned words)
    bucket                str        product / tech / business / dev
    zh_ratio              float      payload-level ZH char ratio (audit)
    dedup_ratio           float      dedup ratio from compactor (audit)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # no schema import at runtime; use getattr throughout

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ZH_THRESHOLD = 0.25   # below this: apply ZH skeleton

_ISO_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

_SENT_SPLIT_RE = re.compile(
    r"(?<=[。！？；])"
    r"|(?<=[.?!])\s+"
    r"|\n+"
)

_INTERNAL_TAG_RE = re.compile(
    r"^(WATCH|TEST|MOVE|FIX|TODO|NOTE)\b\s*[：:]\s*",
    re.IGNORECASE,
)

_BANNED_FRAGS: list[str] = [
    "的趨勢，解決方 記",
    "詳見原始來源",
    "監控中 本欄暫無事件",
    "Evidence summary: sources=",
    "Key terms: ",
    "validate source evidence and related numbers",
    "run small-scope checks against current workflow",
    "escalate only if next scan confirms sustained",
    "此欄暫無資料；持續掃描來源中",
    "本欄暫無資料",
]


# ---------------------------------------------------------------------------
# Low-level text helpers
# ---------------------------------------------------------------------------

def _zh_ratio(text: str) -> float:
    if not text:
        return 0.0
    zh = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return round(zh / max(1, len(text)), 3)


def _ensure_terminated(text: str) -> str:
    t = text.strip()
    if t and t[-1] not in "。！？；.!?":
        t += "。"
    return t


def _clean_raw(text: str) -> str:
    """Strip internal tags and banned template fragments."""
    text = _INTERNAL_TAG_RE.sub("", (text or "").strip())
    for b in _BANNED_FRAGS:
        text = text.replace(b, "")
    return text.strip()


def _split_sentences(text: str) -> list[str]:
    parts = _SENT_SPLIT_RE.split(text or "")
    return [p.strip() for p in parts if p.strip() and len(p.strip()) >= 4]


def _sanitize_text(text: str) -> str:
    """Apply exec_sanitizer if available; else basic clean."""
    if not text:
        return text
    try:
        from utils.exec_sanitizer import sanitize_exec_text
        return sanitize_exec_text(text)
    except Exception:
        return _clean_raw(text)


def _near_dup(a: str, candidates: list[str], threshold: float = 0.75) -> bool:
    return any(SequenceMatcher(None, a, c).ratio() >= threshold for c in candidates)


# ---------------------------------------------------------------------------
# Topic bucket
# ---------------------------------------------------------------------------

def _get_bucket(card) -> str:
    try:
        from utils.topic_router import route_topic
        result = route_topic(card)
        ch = (result.get("channel", "") or "").lower()
        if ch in ("product", "tech", "business", "dev"):
            return ch
    except Exception:
        pass
    cat = (getattr(card, "category", "") or "").lower()
    if any(k in cat for k in ("ai", "tech", "工程", "軟體", "硬體", "晶片")):
        return "tech"
    if any(k in cat for k in ("product", "產品")):
        return "product"
    if any(k in cat for k in ("dev", "開發")):
        return "dev"
    return "business"


# ---------------------------------------------------------------------------
# Proof line
# ---------------------------------------------------------------------------

def _make_proof_line(card) -> str:
    """Build: 證據：來源：{src}（YYYY-MM-DD）"""
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


# ---------------------------------------------------------------------------
# ZH skeleton builders — no new facts, only card-derived text
# ---------------------------------------------------------------------------

def _zh_skeleton_q1(card) -> str:
    """Rebuild ZH-dominant q1 (what happened) from card fields."""
    what = (getattr(card, "what_happened", "") or "").strip()
    tech = (getattr(card, "technical_interpretation", "") or "").strip()
    title = (getattr(card, "title_plain", "") or "").strip()
    base = what or tech or title
    if not base:
        return ""
    sents = _split_sentences(base)
    if sents:
        # Take up to 2 sentences; prefer ZH-heavy ones
        selected: list[str] = []
        for s in sents:
            if _zh_ratio(s) >= _ZH_THRESHOLD:
                selected.append(s)
            if len(selected) >= 2:
                break
        if not selected:
            selected = sents[:2]
        return "".join(_ensure_terminated(s) for s in selected[:2])
    return _ensure_terminated(base[:200])


def _zh_skeleton_q2(card) -> str:
    """Rebuild ZH-dominant q2 (why important) from card fields."""
    why = (getattr(card, "why_important", "") or "").strip()
    effects = getattr(card, "derivable_effects", None) or []
    tech = (getattr(card, "technical_interpretation", "") or "").strip()

    parts: list[str] = []
    if why:
        sents = _split_sentences(why)
        for s in sents:
            if len(parts) >= 2:
                break
            parts.append(s)
    if not parts and effects:
        for eff in effects[:2]:
            s = str(eff or "").strip()
            if s and len(s) >= 8:
                parts.append(s)
    if not parts and tech:
        sents = _split_sentences(tech)
        parts.extend(sents[:2])

    if not parts:
        return ""
    return "".join(_ensure_terminated(s) for s in parts[:2])


def _zh_skeleton_q3(card) -> list[str]:
    """Rebuild ZH-dominant q3 bullets from card.action_items / why_important."""
    actions = getattr(card, "action_items", None) or []
    bullets: list[str] = []

    for a in actions:
        s = _clean_raw(str(a or "").strip())
        if len(s) >= 12 and not _near_dup(s, bullets):
            bullets.append(_ensure_terminated(s))
        if len(bullets) >= 3:
            break

    # Fill from why_important sentences
    if len(bullets) < 3:
        why = (getattr(card, "why_important", "") or "").strip()
        for s in _split_sentences(why):
            if len(bullets) >= 3:
                break
            s_c = _clean_raw(s)
            if len(s_c) >= 12 and not _near_dup(s_c, bullets):
                bullets.append(_ensure_terminated(s_c))

    # Fill from what_happened sentences
    if len(bullets) < 2:
        what = (getattr(card, "what_happened", "") or "").strip()
        for s in _split_sentences(what):
            if len(bullets) >= 3:
                break
            s_c = _clean_raw(s)
            if len(s_c) >= 12 and not _near_dup(s_c, bullets):
                bullets.append(_ensure_terminated(s_c))

    return bullets[:3]


def _zh_skeleton_risks(card) -> list[str]:
    """Rebuild ZH-dominant risk bullets from card.speculative_effects / derivable_effects."""
    speculative = getattr(card, "speculative_effects", None) or []
    bullets: list[str] = []

    for r in speculative:
        s = _clean_raw(str(r or "").strip())
        if len(s) >= 12 and not _near_dup(s, bullets):
            bullets.append(_ensure_terminated(s))
        if len(bullets) >= 2:
            break

    # Fallback to derivable_effects
    if len(bullets) < 2:
        effects = getattr(card, "derivable_effects", None) or []
        for eff in effects:
            s = _clean_raw(str(eff or "").strip())
            if len(s) >= 12 and not _near_dup(s, bullets):
                bullets.append(_ensure_terminated(s))
            if len(bullets) >= 2:
                break

    return bullets[:2]


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_canonical_payload(card) -> dict:
    """Build canonical payload from card.

    Algorithm
    ---------
    1. Call narrative_compactor_v2 for base narrative + bullets + proof + stats.
    2. Split narrative → q1 (sentences 0-1) + q2 (sentences 2+).
    3. If zh_ratio(q1) < 0.25: apply ZH skeleton for q1.
    4. If zh_ratio(q2) < 0.25 or q2 empty: apply ZH skeleton for q2.
    5. Build q3 from compactor bullets; fill with ZH skeleton if < 2.
    6. Build risks from ZH skeleton (speculative_effects → derivable_effects).
    7. sanitize_exec_text on all string fields.
    8. Return fixed-schema dict.
    """
    # ── Step 1: narrative_compactor_v2 ──────────────────────────────────────
    try:
        from utils.narrative_compactor_v2 import build_narrative_v2
        comp = build_narrative_v2(card)
        narrative = comp.get("narrative_2to3_sentences_zh", "") or ""
        comp_bullets = comp.get("bullets_2to3", []) or []
        proof = comp.get("proof_line", "") or ""
        debug = comp.get("debug_stats", {}) or {}
        dedup_ratio = float(debug.get("dedup_ratio", 0.0))
    except Exception:
        narrative = ""
        comp_bullets = []
        proof = ""
        dedup_ratio = 0.0

    # ── Step 2: Split narrative → q1 / q2 ───────────────────────────────────
    all_sents = _split_sentences(narrative)
    if len(all_sents) >= 2:
        q1_raw = _ensure_terminated(all_sents[0])
        q2_raw = "".join(_ensure_terminated(s) for s in all_sents[1:])
    elif len(all_sents) == 1:
        q1_raw = _ensure_terminated(all_sents[0])
        q2_raw = ""
    else:
        q1_raw = ""
        q2_raw = ""

    # ── Step 3-4: ZH skeleton if ratio too low ──────────────────────────────
    if _zh_ratio(q1_raw) < _ZH_THRESHOLD:
        skel = _zh_skeleton_q1(card)
        if skel:
            q1_raw = skel

    if _zh_ratio(q2_raw) < _ZH_THRESHOLD or not q2_raw:
        skel = _zh_skeleton_q2(card)
        if skel:
            q2_raw = skel

    # Ensure q1 always has content
    if not q1_raw.strip():
        q1_raw = _zh_skeleton_q1(card) or "事件摘要：詳細資訊待確認。"

    # Ensure q2 always has content distinct from q1
    if not q2_raw.strip() or SequenceMatcher(None, q1_raw, q2_raw).ratio() > 0.85:
        q2_raw = _zh_skeleton_q2(card) or "影響評估：此事件的商業影響待進一步確認。"

    # ── Step 5: q3 bullets ───────────────────────────────────────────────────
    q3: list[str] = []
    for b in comp_bullets:
        b_c = _clean_raw(str(b or "").strip())
        if len(b_c) >= 12 and not _near_dup(b_c, q3):
            q3.append(_ensure_terminated(b_c))
        if len(q3) >= 3:
            break

    if len(q3) < 2:
        for s in _zh_skeleton_q3(card):
            if not _near_dup(s, q3):
                q3.append(s)
            if len(q3) >= 3:
                break

    if not q3:
        q3 = ["持續監控此事件後續發展（T+7）。"]

    # ── Step 6: risks ────────────────────────────────────────────────────────
    risks = _zh_skeleton_risks(card)
    if not risks:
        risks = ["重要性評分：待確認。"]

    # ── Step 7: proof ────────────────────────────────────────────────────────
    if not proof:
        proof = _make_proof_line(card)

    # ── Step 8: title_clean ──────────────────────────────────────────────────
    raw_title = (getattr(card, "title_plain", "") or "").strip()
    try:
        from core.content_strategy import sanitize as _cs_sanitize
        title_clean = _cs_sanitize(raw_title) or raw_title
    except Exception:
        title_clean = _clean_raw(raw_title)

    # ── Step 9: bucket ───────────────────────────────────────────────────────
    bucket = _get_bucket(card)

    # ── Step 10: sanitize all text ───────────────────────────────────────────
    q1_final = _sanitize_text(q1_raw) or "事件摘要：詳細資訊待確認。"
    q2_final = _sanitize_text(q2_raw) or "影響評估：此事件的商業影響待進一步確認。"
    q3_final = [_sanitize_text(b) for b in q3 if _sanitize_text(b)]
    risks_final = [_sanitize_text(r) for r in risks if _sanitize_text(r)]

    if not q3_final:
        q3_final = ["持續監控此事件後續發展（T+7）。"]
    if not risks_final:
        risks_final = ["重要性評分：待確認。"]

    # ── Step 11: final zh_ratio ──────────────────────────────────────────────
    combined = " ".join([q1_final, q2_final] + q3_final + risks_final)
    final_zh_ratio = _zh_ratio(combined)

    return {
        "q1_event_2sent_zh": q1_final,
        "q2_impact_2sent_zh": q2_final,
        "q3_moves_3bullets_zh": q3_final[:3],
        "risks_2bullets_zh": risks_final[:2],
        "proof_line": proof,
        "title_clean": title_clean or raw_title,
        "bucket": bucket,
        "zh_ratio": final_zh_ratio,
        "dedup_ratio": dedup_ratio,
    }


def get_canonical_payload(card) -> dict:
    """Return canonical payload; cache result on card._canonical_payload_v3 (runtime only).

    Does NOT modify card schema — attaches runtime attribute only.
    """
    cached = getattr(card, "_canonical_payload_v3", None)
    if cached is not None:
        return cached

    payload = build_canonical_payload(card)

    # Cache with setattr; dataclasses without __slots__ allow this
    try:
        setattr(card, "_canonical_payload_v3", payload)
    except Exception:
        pass  # non-fatal if card is frozen/immutable

    return payload
