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

def _build_rewrite_context(card, bucket: str) -> dict:
    """Build context dict for newsroom_zh_rewrite from card fields."""
    # Extract ISO date from card
    date_str = ""
    for attr in ("published_at", "published_at_parsed", "collected_at"):
        val = getattr(card, attr, None)
        if val:
            m = _ISO_DATE_RE.search(str(val))
            if m:
                date_str = m.group(0)
                break

    title = (getattr(card, "title_plain", "") or "").strip()
    return {
        "title": title,
        "date": date_str,
        "subject": "",  # filled below
        "bucket": bucket,
        "source": (getattr(card, "source_name", "") or "").strip(),
        "what_happened": (getattr(card, "what_happened", "") or "").strip(),
        "why_important": (getattr(card, "why_important", "") or "").strip(),
        "action_items": list(getattr(card, "action_items", None) or []),
        "speculative_effects": list(getattr(card, "speculative_effects", None) or []),
        "derivable_effects": list(getattr(card, "derivable_effects", None) or []),
    }


def build_canonical_payload(card) -> dict:
    """Build canonical payload from card — Iteration 4: News Anchor Perfect v1.

    Algorithm
    ---------
    1. Call narrative_compactor_v2 for base narrative + bullets + proof + stats.
    2. Extract concrete anchors from card (news_anchor.extract_anchors_from_card).
    3. Build rewrite context dict from card fields.
    4. Call newsroom_zh_rewrite v2 for q1/q2 (anchor-injected);
       v1 for q3/risks (unchanged).
    5. Verify zh_ratio >= 0.20 for each field; fall back to ZH skeleton if needed.
    6. proof_line from compactor or _make_proof_line.
    7. sanitize_exec_text on all fields.
    8. Return fixed-schema dict with anchor debug fields.
    """
    # ── Step 1: narrative_compactor_v2 (for proof + dedup stats) ────────────
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

    # ── Step 2: Extract concrete anchors (Iteration 4) ───────────────────────
    _anchors: list[str] = []
    _anchor_types: dict = {}
    _primary_anchor: str | None = None
    _has_anchor: bool = False
    try:
        from utils.news_anchor import (
            extract_anchors_from_card as _eafc,
            pick_primary_anchor as _ppa,
        )
        _anchor_result = _eafc(card)
        _anchors       = _anchor_result.get("anchors", []) or []
        _anchor_types  = _anchor_result.get("anchor_types", {}) or {}
        _has_anchor    = bool(_anchor_result.get("has_anchor", False))
        _primary_anchor = _ppa(_anchors, _anchor_types) if _anchors else None
    except Exception:
        pass

    # ── Step 3: bucket + rewrite context ─────────────────────────────────────
    bucket = _get_bucket(card)
    ctx = _build_rewrite_context(card, bucket)

    # ── Step 4: newsroom ZH rewrite v2 (anchor-injected) ─────────────────────
    try:
        from utils.newsroom_zh_rewrite import (
            rewrite_news_lead_v2 as _nzh_lead,
            rewrite_news_impact_v2 as _nzh_impact,
            rewrite_news_next as _nzh_next,
            rewrite_news_risks as _nzh_risks,
            zh_ratio as _nzh_ratio,
        )
        q1_raw = _nzh_lead(
            narrative, ctx,
            anchors=_anchors, primary_anchor=_primary_anchor,
        )
        q2_raw = _nzh_impact(
            narrative, ctx,
            anchors=_anchors, primary_anchor=_primary_anchor,
        )
        q3_bullets = _nzh_next(narrative, ctx)
        risks_bullets = _nzh_risks(narrative, ctx)
        _newsroom_active = True
    except Exception:
        _newsroom_active = False
        q1_raw = ""
        q2_raw = ""
        q3_bullets = []
        risks_bullets = []

    # ── Step 4: fallback to ZH skeleton if newsroom output is too English ────
    _ZH_MIN = 0.20

    if not q1_raw or _zh_ratio(q1_raw) < _ZH_MIN:
        skel = _zh_skeleton_q1(card)
        if skel and _zh_ratio(skel) >= _ZH_MIN:
            q1_raw = skel
        elif not q1_raw:
            q1_raw = "事件摘要：詳細資訊待確認。"

    if not q2_raw or _zh_ratio(q2_raw) < _ZH_MIN:
        skel = _zh_skeleton_q2(card)
        if skel and _zh_ratio(skel) >= _ZH_MIN:
            q2_raw = skel
        elif not q2_raw:
            q2_raw = "影響評估：此事件的商業影響待進一步確認。"

    # Ensure q2 is distinct from q1
    if SequenceMatcher(None, q1_raw, q2_raw).ratio() > 0.85:
        skel = _zh_skeleton_q2(card)
        if skel:
            q2_raw = skel

    # q3 bullets — prefer newsroom output, fill with skeleton
    q3: list[str] = []
    for b in q3_bullets:
        b_c = _clean_raw(str(b or "").strip())
        if len(b_c) >= 12 and not _near_dup(b_c, q3):
            q3.append(_ensure_terminated(b_c))
        if len(q3) >= 3:
            break

    # Fill with compactor bullets if newsroom q3 is sparse
    if len(q3) < 2:
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

    # risks — prefer newsroom output, fill with skeleton
    risks: list[str] = []
    for r in risks_bullets:
        r_c = _clean_raw(str(r or "").strip())
        if len(r_c) >= 12 and not _near_dup(r_c, risks):
            risks.append(_ensure_terminated(r_c))
        if len(risks) >= 2:
            break

    if len(risks) < 2:
        for s in _zh_skeleton_risks(card):
            if not _near_dup(s, risks):
                risks.append(s)
            if len(risks) >= 2:
                break

    if not risks:
        risks = ["重要性評分：待確認。"]

    # ── Step 5: proof ────────────────────────────────────────────────────────
    if not proof:
        proof = _make_proof_line(card)

    # ── Step 6: title_clean ──────────────────────────────────────────────────
    raw_title = (getattr(card, "title_plain", "") or "").strip()
    try:
        from core.content_strategy import sanitize as _cs_sanitize
        title_clean = _cs_sanitize(raw_title) or raw_title
    except Exception:
        title_clean = _clean_raw(raw_title)

    # ── Step 7: sanitize all text ─────────────────────────────────────────────
    q1_final = _sanitize_text(q1_raw) or "事件摘要：詳細資訊待確認。"
    q2_final = _sanitize_text(q2_raw) or "影響評估：此事件的商業影響待進一步確認。"
    q3_final = [_sanitize_text(b) for b in q3 if _sanitize_text(b)]
    risks_final = [_sanitize_text(r) for r in risks if _sanitize_text(r)]

    if not q3_final:
        q3_final = ["持續監控此事件後續發展（T+7）。"]
    if not risks_final:
        risks_final = ["重要性評分：待確認。"]

    # ── Step 8: final zh_ratio ────────────────────────────────────────────────
    combined = " ".join([q1_final, q2_final] + q3_final[:3] + risks_final[:2])
    final_zh_ratio = _zh_ratio(combined)

    # ── Step 9 (Iteration 5): faithful_zh_news override (EN source, Ollama) ──
    # Condition: source text is mostly English (zh_ratio < 0.25) AND >= 1200 chars.
    # Non-fatal: any exception falls through to original output.
    _faithful_applied = False
    try:
        from utils.faithful_zh_news import (
            build_source_text as _fzh_src,
            generate_faithful_zh as _fzh_gen,
            _zh_ratio as _fzh_zhr,
        )
        _src_text = _fzh_src(card)
        _src_en   = len(_src_text) >= 1200 and _fzh_zhr(_src_text) < 0.25
        if _src_en:
            _fzh = _fzh_gen(card, source_text=_src_text)
            if _fzh is not None:
                q1_final       = _fzh.get("q1", q1_final) or q1_final
                q2_final       = _fzh.get("q2", q2_final) or q2_final
                _fzh_q3        = _fzh.get("q3_bullets", [])
                if _fzh_q3:
                    q3_final   = _fzh_q3[:3]
                proof          = _fzh.get("proof_line", proof) or proof
                # Merge anchors: faithful anchors take precedence
                _fzh_anchors   = _fzh.get("anchors_top3", [])
                if _fzh_anchors:
                    _anchors   = _fzh_anchors + [a for a in _anchors if a not in _fzh_anchors]
                    _has_anchor = True
                # Recompute zh_ratio and _primary_anchor with faithful output
                combined       = " ".join([q1_final, q2_final] + q3_final[:3])
                final_zh_ratio = _zh_ratio(combined)
                if _anchors:
                    from utils.news_anchor import pick_primary_anchor as _ppa2
                    _primary_anchor = _ppa2(_anchors, _anchor_types) or _anchors[0]
                _faithful_applied = True
                # Store faithful meta on card (non-schema, runtime only)
                try:
                    setattr(card, "_faithful_zh_result", _fzh)
                except Exception:
                    pass
    except Exception:
        pass  # non-fatal; original output remains

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
        "newsroom_rewrite": _newsroom_active,
        "faithful_zh_applied": _faithful_applied,
        # Iteration 4 anchor debug fields
        "anchor_missing": not _has_anchor,
        "primary_anchor": _primary_anchor or "",
        "anchors_top3": _anchors[:3],
        "anchor_types": _anchor_types,
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


def write_news_anchor_meta(cards: list, outdir: "str | None" = None) -> None:
    """Write outputs/news_anchor.meta.json — Iteration 4 anchor audit file.

    Reads anchor debug fields from cached _canonical_payload_v3 on each card.
    Computes coverage statistics and writes samples for verify_online.ps1
    NEWS_ANCHOR_GATE inspection.
    """
    import json
    from pathlib import Path
    from datetime import datetime, timezone

    root = Path(outdir) if outdir else Path(__file__).resolve().parent.parent / "outputs"
    root.mkdir(parents=True, exist_ok=True)

    events_total       = 0
    anchor_present     = 0
    anchor_missing_ids: list[str] = []
    anchor_type_counts: dict[str, int] = {}
    samples: list[dict] = []

    for card in cards:
        cp = getattr(card, "_canonical_payload_v3", None)
        if cp is None:
            try:
                cp = get_canonical_payload(card)
            except Exception:
                continue
        if cp is None:
            continue

        events_total += 1
        is_missing = bool(cp.get("anchor_missing", True))
        cid = (getattr(card, "item_id", "") or (getattr(card, "title_plain", "") or "")[:30])

        if is_missing:
            if len(anchor_missing_ids) < 5:
                anchor_missing_ids.append(str(cid))
        else:
            anchor_present += 1

        # Aggregate anchor type counts
        for atype, count in (cp.get("anchor_types", {}) or {}).items():
            anchor_type_counts[atype] = anchor_type_counts.get(atype, 0) + count

        # Collect samples: up to 3, prefer anchor-present
        if len(samples) < 3 and not is_missing:
            samples.append({
                "item_id":       str(cid)[:30],
                "title":         cp.get("title_clean", "")[:60],
                "primary_anchor": cp.get("primary_anchor", ""),
                "anchors_top3":  cp.get("anchors_top3", []),
                "anchor_types":  cp.get("anchor_types", {}),
                "q1":            cp.get("q1_event_2sent_zh", "")[:220],
                "q2":            cp.get("q2_impact_2sent_zh", "")[:220],
                "proof":         cp.get("proof_line", ""),
                "zh_ratio":      cp.get("zh_ratio", 0.0),
            })

    anchor_missing_count = events_total - anchor_present
    coverage_ratio = round(anchor_present / events_total, 3) if events_total else 1.0

    meta = {
        "generated_at":           datetime.now(timezone.utc).isoformat(),
        "events_total":           events_total,
        "anchor_present_count":   anchor_present,
        "anchor_missing_count":   anchor_missing_count,
        "anchor_coverage_ratio":  coverage_ratio,
        "missing_event_ids_top5": anchor_missing_ids[:5],
        "top_anchor_types_count": anchor_type_counts,
        "samples":                samples,
    }

    try:
        (root / "news_anchor.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass  # non-fatal


def write_newsroom_zh_meta(cards: list, outdir: "str | None" = None) -> None:
    """Write outputs/newsroom_zh.meta.json — Iteration 3 audit file.

    Collects zh_ratio per card from cached _canonical_payload_v3, computes
    aggregate statistics, and writes a samples array (≥1 event) for
    verify_online.ps1 NEWSROOM_ZH GATE sampling.
    """
    import json
    from pathlib import Path
    from datetime import datetime, timezone

    root = Path(outdir) if outdir else Path(__file__).resolve().parent.parent / "outputs"
    root.mkdir(parents=True, exist_ok=True)

    ratios: list[float] = []
    samples: list[dict] = []

    for card in cards:
        cp = getattr(card, "_canonical_payload_v3", None)
        if cp is None:
            # Trigger build if not cached yet
            try:
                cp = get_canonical_payload(card)
            except Exception:
                continue
        if cp is None:
            continue

        ratio = float(cp.get("zh_ratio", 0.0))
        ratios.append(ratio)

        # Collect sample (up to 3 events with highest zh_ratio for display)
        if len(samples) < 3:
            samples.append({
                "item_id":   (getattr(card, "item_id", "") or "")[:20],
                "title":     cp.get("title_clean", "")[:60],
                "q1":        cp.get("q1_event_2sent_zh", "")[:200],
                "q2":        cp.get("q2_impact_2sent_zh", "")[:200],
                "q3":        cp.get("q3_moves_3bullets_zh", [])[:3],
                "proof":     cp.get("proof_line", ""),
                "zh_ratio":  ratio,
                "newsroom":  cp.get("newsroom_rewrite", False),
            })

    count = len(ratios)
    avg_zh = round(sum(ratios) / count, 3) if count else 0.0
    min_zh = round(min(ratios), 3) if ratios else 0.0

    meta = {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "applied_count":  count,
        "avg_zh_ratio":   avg_zh,
        "min_zh_ratio":   min_zh,
        "samples":        samples,
    }

    try:
        (root / "newsroom_zh.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass  # non-fatal
