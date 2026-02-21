"""utils/longform_narrative.py — BBC-style Anti-Fragment Longform Narrative v1.

Stdlib-only (no new pip deps).

Public API:
    pick_anchor_text(card)       -> str | None
    extract_key_sentences(text)  -> list[str]
    build_sections(card, sents)  -> dict[str, str]
    render_bbc_longform(card)    -> dict
    write_longform_meta(outdir)  -> None
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas.education_models import EduNewsCard

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MIN_ANCHOR_CHARS: int = int(os.environ.get("LONGFORM_MIN_ANCHOR_CHARS", "1200"))
_CACHE_ATTR = "_longform_v1_cache"

# Patterns for verifiable proof tokens (searched in order; first match wins)
_PROOF_PATTERNS: list[re.Pattern] = [
    re.compile(r"\barXiv:\d{4}\.\d{4,5}\b"),                          # arXiv:2402.10055
    re.compile(r"\bv\d+\.\d+(?:\.\d+)*\b"),                           # v1.2.3
    re.compile(r"\$\d+(?:\.\d+)?[BMK]\b", re.I),                      # $500M / $1.2B
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),                             # 2026-02-21
    re.compile(r"\b\d{1,3}(?:\.\d+)?\s*[BMK]\s+param", re.I),        # 7B params
    re.compile(r"\b(?:MMLU|HumanEval|MATH|BigBench|HELM|MT-Bench)\b"),# benchmarks
    re.compile(r"\b\d{1,3}(?:\.\d+)?\s*%\b"),                         # 94.5%
]

# ---------------------------------------------------------------------------
# Module-level stats accumulator (thread-safe, reset per pipeline run)
# ---------------------------------------------------------------------------

_stats_lock = threading.Lock()
_stats: dict = {
    "total_cards_processed": 0,
    "eligible_count": 0,
    "ineligible_count": 0,
    "proof_present_count": 0,
    "proof_missing_count": 0,
    "anchor_chars_sum": 0,
    "samples": [],
}


def reset_stats() -> None:
    """Reset accumulator (call at the start of each pipeline run if needed)."""
    with _stats_lock:
        _stats.update({
            "total_cards_processed": 0,
            "eligible_count": 0,
            "ineligible_count": 0,
            "proof_present_count": 0,
            "proof_missing_count": 0,
            "anchor_chars_sum": 0,
            "samples": [],
        })


# ---------------------------------------------------------------------------
# pick_anchor_text
# ---------------------------------------------------------------------------

def pick_anchor_text(card: "EduNewsCard") -> str | None:
    """Combine all rich text fields from card; return combined if >= MIN_ANCHOR_CHARS.

    Fields used (richest first):
        what_happened, why_important, technical_interpretation,
        derivable_effects, speculative_effects, observation_metrics,
        action_items, evidence_lines, fact_check_confirmed,
        fact_check_unverified, title_plain, metaphor, focus_action
    """
    parts: list[str] = []

    for field_name in ("what_happened", "why_important", "technical_interpretation"):
        val = getattr(card, field_name, "") or ""
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())

    for field_name in (
        "derivable_effects",
        "speculative_effects",
        "observation_metrics",
        "action_items",
        "evidence_lines",
        "fact_check_confirmed",
        "fact_check_unverified",
    ):
        val = getattr(card, field_name, None) or []
        if isinstance(val, list):
            joined = " ".join(str(v) for v in val if v)
            if joined.strip():
                parts.append(joined.strip())

    for field_name in ("title_plain", "metaphor", "focus_action"):
        val = getattr(card, field_name, "") or ""
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())

    combined = " ".join(parts)
    return combined if len(combined) >= MIN_ANCHOR_CHARS else None


# ---------------------------------------------------------------------------
# extract_key_sentences
# ---------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+")
_SIGNAL_WORDS = re.compile(
    r"\b(?:launch|release|announce|introduce|deploy|raise|fund|acquire|partner|"
    r"benchmark|achieve|surpass|outperform|demonstrate|show|find|conclude|estimate|"
    r"available|shipped|open.source|API|SDK|model|revenue|billion|million|trillion)\b",
    re.I,
)
_NUMBER_PATTERN = re.compile(
    r"\b\d+(?:[,.]\d+)*\s*(?:%|[BMK]|ms|GB|MB|B|M|params?|tokens?)?\b", re.I
)


def extract_key_sentences(text: str) -> list[str]:
    """Split text into sentences, score them, return top 7 in original order."""
    raw_sentences = _SENT_SPLIT.split(text)
    scored: list[tuple[float, int, str]] = []

    for idx, sent in enumerate(raw_sentences):
        s = sent.strip()
        if len(s) < 30 or len(s) > 400:
            continue
        word_count = len(s.split())

        score = 0.0
        # Prefer medium-length sentences
        if 15 <= word_count <= 50:
            score += 2.0
        elif 8 <= word_count <= 70:
            score += 1.0

        # Numeric hits are strong evidence of specificity
        nums = _NUMBER_PATTERN.findall(s)
        score += min(len(nums) * 1.5, 4.5)

        # Signal word hits
        sigs = _SIGNAL_WORDS.findall(s)
        score += min(len(sigs) * 1.0, 3.0)

        # First-quarter bonus (provides opening context)
        if raw_sentences and idx < max(1, len(raw_sentences) // 4):
            score += 0.5

        scored.append((score, idx, s))

    # Sort by score desc, take top 7, restore original order
    top = sorted(scored, key=lambda x: -x[0])[:7]
    top_ordered = sorted(top, key=lambda x: x[1])

    seen: set[str] = set()
    result: list[str] = []
    for _, _, s in top_ordered:
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _join_field(card: "EduNewsCard", name: str, sep: str = " ") -> str:
    val = getattr(card, name, None)
    if not val:
        return ""
    if isinstance(val, list):
        return sep.join(str(v) for v in val if v)
    return str(val)


def _find_proof_token(text: str) -> str | None:
    """Return the first verifiable proof token found in text, or None."""
    for pat in _PROOF_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


# ---------------------------------------------------------------------------
# build_sections
# ---------------------------------------------------------------------------

def build_sections(card: "EduNewsCard", key_sents: list[str]) -> dict[str, str]:
    """Build 5 BBC sections from card fields + extracted key sentences.

    Sections:
        bg      — 背景 (Background / What happened)
        what_is — 這是什麼 (Technical identity)
        why     — 為何重要 (Why it matters)
        risks   — 爭議/風險 (Risks & caveats)
        next    — 下一步 T+7 (Observable next actions)
    """
    # --- bg: what happened ---
    bg_base = _join_field(card, "what_happened")
    extra_sents = [s for s in key_sents if bg_base and s not in bg_base][:2]
    bg = (bg_base + " " + " ".join(extra_sents)).strip()
    if not bg and key_sents:
        bg = " ".join(key_sents[:2])
    bg = bg or "（背景待補充）"

    # --- what_is: technical interpretation + category ---
    cat = _join_field(card, "category")
    tech = _join_field(card, "technical_interpretation")
    what_is_parts = []
    if cat:
        what_is_parts.append(f"分類：{cat}。")
    if tech:
        what_is_parts.append(tech[:350])
    elif len(key_sents) > 1:
        what_is_parts.append(" ".join(key_sents[1:3]))
    what_is = " ".join(what_is_parts).strip() or "（技術解讀待補充）"

    # --- why: importance + first-order effects ---
    why_base = _join_field(card, "why_important")
    deriv = _join_field(card, "derivable_effects", sep="; ")
    why_parts = [p for p in [why_base, deriv] if p]
    why = " ".join(why_parts).strip()
    if not why and len(key_sents) > 2:
        why = " ".join(key_sents[2:4])
    why = why or "（重要性待補充）"

    # --- risks: speculative effects + unverified claims ---
    spec = _join_field(card, "speculative_effects", sep="; ")
    unver = _join_field(card, "fact_check_unverified", sep="; ")
    risk_parts = [p for p in [spec, unver] if p]
    risks = " ".join(risk_parts).strip() or "（風險評估待補充）"

    # --- next: observation metrics + action items ---
    obs = _join_field(card, "observation_metrics", sep="; ")
    acts = _join_field(card, "action_items", sep="; ")
    next_parts = [p for p in [obs, acts] if p]
    nxt = " ".join(next_parts).strip() or "（行動計畫待補充）"

    return {"bg": bg, "what_is": what_is, "why": why, "risks": risks, "next": nxt}


# ---------------------------------------------------------------------------
# render_bbc_longform  (main entry point)
# ---------------------------------------------------------------------------

def render_bbc_longform(card: "EduNewsCard") -> dict:
    """Render BBC-style longform narrative for a card.

    Returns dict with keys:
        bg, what_is, why, risks, next,
        proof_line, proof_missing,
        eligible, anchor_chars

    Result is cached on card via _longform_v1_cache attribute to prevent
    double-computation when build_ceo_brief_blocks() is called twice per card
    (Slide A and Slide B).
    """
    # Return cached result if already computed (cache prevents double stats)
    cached = getattr(card, _CACHE_ATTR, None)
    if cached is not None:
        return cached

    anchor = pick_anchor_text(card)
    eligible = anchor is not None
    anchor_chars = len(anchor) if anchor else 0

    if eligible:
        key_sents = extract_key_sentences(anchor)
        sections = build_sections(card, key_sents)
        proof_token = _find_proof_token(anchor)
        proof_line = proof_token or ""
        proof_missing = proof_token is None
    else:
        # Ineligible: build minimal fallback from shortest fields
        sections = {
            "bg": _join_field(card, "what_happened") or _join_field(card, "title_plain"),
            "what_is": _join_field(card, "technical_interpretation") or "",
            "why": _join_field(card, "why_important") or "",
            "risks": "",
            "next": _join_field(card, "focus_action") or "",
        }
        proof_line = ""
        proof_missing = True

    result: dict = {
        "bg": sections["bg"],
        "what_is": sections["what_is"],
        "why": sections["why"],
        "risks": sections["risks"],
        "next": sections["next"],
        "proof_line": proof_line,
        "proof_missing": proof_missing,
        "eligible": eligible,
        "anchor_chars": anchor_chars,
    }

    # Cache on card object (dataclass; use object.__setattr__ to bypass frozen check)
    try:
        object.__setattr__(card, _CACHE_ATTR, result)
    except (AttributeError, TypeError):
        pass

    # Accumulate stats (only fires once per card due to cache guard above)
    with _stats_lock:
        _stats["total_cards_processed"] += 1
        if eligible:
            _stats["eligible_count"] += 1
            _stats["anchor_chars_sum"] += anchor_chars
            if not proof_missing:
                _stats["proof_present_count"] += 1
            else:
                _stats["proof_missing_count"] += 1
            if len(_stats["samples"]) < 3:
                _stats["samples"].append({
                    "title": (getattr(card, "title_plain", "") or "")[:80],
                    "anchor_chars": anchor_chars,
                    "proof_line": proof_line,
                    "eligible": True,
                })
        else:
            _stats["ineligible_count"] += 1
            _stats["proof_missing_count"] += 1

    return result


# ---------------------------------------------------------------------------
# write_longform_meta
# ---------------------------------------------------------------------------

def write_longform_meta(outdir: "str | Path | None" = None) -> None:
    """Write outputs/exec_longform.meta.json from accumulated stats."""
    if outdir is None:
        outdir = Path(__file__).parent.parent / "outputs"
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    with _stats_lock:
        total = _stats["total_cards_processed"]
        eligible = _stats["eligible_count"]
        ineligible = _stats["ineligible_count"]
        proof_present = _stats["proof_present_count"]
        proof_missing = _stats["proof_missing_count"]
        anchor_sum = _stats["anchor_chars_sum"]
        samples = list(_stats["samples"])

    eligible_ratio = round(eligible / total, 3) if total else 0.0
    proof_ratio = round(proof_present / eligible, 3) if eligible else 0.0
    avg_anchor = round(anchor_sum / eligible, 1) if eligible else 0.0

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "min_anchor_chars": MIN_ANCHOR_CHARS,
        "total_cards_processed": total,
        "eligible_count": eligible,
        "ineligible_count": ineligible,
        "proof_present_count": proof_present,
        "proof_missing_count": proof_missing,
        "eligible_ratio": eligible_ratio,
        "proof_coverage_ratio": proof_ratio,
        "avg_anchor_chars": avg_anchor,
        "samples": samples,
    }

    dest = out / "exec_longform.meta.json"
    dest.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
