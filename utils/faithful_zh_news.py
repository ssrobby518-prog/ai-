"""utils/faithful_zh_news.py — Rule-based faithful extractive ZH news (Iteration 5.2).

Stdlib-only. No LLM / Ollama / llama-server dependency. Works unconditionally.

Design (Iteration 5.2 — Faithful News Enforcement v1):
  - decide_source_text(card) -> str
      Builds EN source text from card fields; title+summary fallback when short.
  - is_english_dominant(text) -> bool
      zh_ratio < 0.35 (relaxed from 0.25; captures near-bilingual cards).
  - should_apply_faithful(card) -> bool
      english_dominant AND len(source_text) >= MIN_CHARS_FOR_FAITHFUL (default 450).
  - extract_quote_tokens(sentence, anchors) -> list[str]
      Verbatim tokens from existing anchors or regex; at most 2 per call.
  - generate_faithful_zh_v2(card, q1_zh, q2_zh, q3_zh, anchors, source_text) -> dict | None
      Produces Q1/Q2/Q3 with injected verbatim quote tokens. Rule-based; never None
      when should_apply_faithful is True. No ellipsis ("..." or U+2026) in output.
  - generate_faithful_zh(card, source_text) -> dict | None
      Backward-compat shim -> generate_faithful_zh_v2.
  - build_source_text(card) -> str
      Backward-compat alias -> decide_source_text.
  - write_faithful_zh_news_meta(results, events_total, applied_fail_count, outdir) -> None
      Writes outputs/faithful_zh_news.meta.json (Iteration 5.2 schema with
      quote_coverage_ratio, ellipsis_hits_total, applied_fail_count, sample_1).

Output format per Q1/Q2 sentence:
    {ZH intro (<=22 chars)}：「{verbatim EN token}」（{ZH context}）。

Iteration 5.2 gate targets:
    applied_count >= 4  (non-sparse-day)
    quote_coverage_ratio >= 0.90  (quote_present_count / applied_count)
    ellipsis_hits_total = 0
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_MIN_CHARS_FOR_FAITHFUL: int = int(os.environ.get("MIN_CHARS_FOR_FAITHFUL", "450"))
_ZH_DOMINANT_THRESHOLD: float = 0.35   # EN-dominant when zh_ratio < this

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_ELLIPSIS_RE = re.compile(r"\u2026|\.{3,}")   # U+2026 or three-plus dots

# Anchor token regex — verbatim substrings from source (model names, money, etc.)
_ANCHOR_KW = re.compile(
    r"(?:"
    r"GPT[-\s]*\d+(?:\.\d+)*(?:[-\s]+(?:mini|turbo|nano|preview|omni|vision|realtime))?"
    r"|Claude[-\s]+\d+(?:\.\d+)*(?:[-\s]+(?:Sonnet|Haiku|Opus|Instant|Core|Edge))?"
    r"|Gemini[-\s]+\d+(?:\.\d+)*(?:[-\s]+(?:Flash|Pro|Ultra|Nano|Advanced|Experimental))?"
    r"|Llama[-\s]+\d+(?:\.\d+)*(?:[-.\s]+\d+[BM])?"
    r"|Qwen\s*\d+(?:\.\d+)*(?:[-\s]+\d+[BM])?"
    r"|DeepSeek[-\s]*(?:R|V|Coder|Chat|MoE)?(?:\d+(?:\.\d+)*)?"
    r"|Phi[-\s]+\d+(?:\.\d+)*(?:[-\s]+(?:mini|small|medium|vision))?"
    r"|Mistral[-\s]*(?:\d+[BM]?)?(?:[-\s]+(?:Large|Small|Nemo|Instruct|Codestral))?"
    r"|Grok[-\s]*\d+(?:\.\d+)*(?:[-\s]+(?:mini|vision|heavy))?"
    r"|SWE-bench(?:\s+Verified)?"
    r"|HumanEval|MMLU|GPQA|MATH(?:bench)?|BIG-Bench"
    r"|\$\s*\d[\d,.]*\s*(?:billion|million|bn|B|M|m|k)?\b"
    r"|\d[\d,.]*\s*(?:billion|million)\b"
    r"|\d+(?:\.\d+)?%"
    r"|v\d+(?:\.\d+){1,3}\b"
    r"|\b\d+[BM]\s+(?:parameters?|params?)\b"
    r"|\bR\d\b|\bV\d\b"
    r")",
    re.IGNORECASE,
)

# Impact keywords — for Q2 sentence / token selection
_IMPACT_KW = re.compile(
    r"\b(?:cost|price|pricing|partner|partnership|regulation|regulatory|"
    r"security|breach|compliance|performance|latency|throughput|"
    r"benchmark|revenue|funding|investment|acqui|deploy|adoption|"
    r"compet|competition|risk|threat|growth|decline|profit|loss|"
    r"margin|valuation|savings|efficiency|capex|opex|ARR|MRR)\b",
    re.IGNORECASE,
)

# Sentence splitters
_ZH_SENT_RE = re.compile(r"(?<=[。！？；])")
_EN_SENT_RE = re.compile(r"(?<=[.!?])\s+|\n{2,}")

# Generic hollow phrases (must not appear in output)
_GENERIC_PHRASES: list[str] = [
    "引發關注", "重要意義", "密切追蹤", "參考基準",
    "值得關注", "廣泛影響", "持續關注", "深遠影響",
    "業界矚目", "市場焦點", "重大進展", "關鍵時刻",
    "值得注意", "引人關注", "不可忽視",
]

# Big Tech — NOT glossed (per pipeline constraint)
NO_GLOSS_TERMS: frozenset[str] = frozenset({
    "Apple", "Google", "Microsoft", "Meta", "Amazon", "OpenAI",
    "Anthropic", "NVIDIA", "DeepSeek", "Mistral", "Cohere",
    "Hugging Face", "xAI", "Samsung", "Baidu", "Alibaba",
    "Tencent", "ByteDance", "Netflix", "Salesforce", "Oracle",
    "Intel", "AMD", "Qualcomm", "IBM", "Cisco",
})


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

def _zh_ratio(text: str) -> float:
    if not text:
        return 0.0
    zh = len(_CJK_RE.findall(text))
    asc = sum(1 for c in text if c.isascii() and c.isalpha())
    total = zh + asc
    return round(zh / total, 3) if total else 0.0


def _remove_ellipsis(text: str) -> str:
    """Hard-strip all ellipsis characters: U+2026 and three-plus dots."""
    return _ELLIPSIS_RE.sub("", text)


def _clean(val: Any) -> str:
    return _remove_ellipsis(str(val or "")).strip()


def _split_zh_sents(text: str) -> list[str]:
    parts = _ZH_SENT_RE.split(_clean(text))
    return [p.strip() for p in parts if len(p.strip()) >= 8]


def _split_en_sents(text: str) -> list[str]:
    parts = _EN_SENT_RE.split(_clean(text).replace("\n", " "))
    return [p.strip() for p in parts if len(p.strip()) >= 10]


def _has_quote(line: str) -> bool:
    """Check if line already contains a 「...」 verbatim token."""
    return bool(re.search(r"「[^」]{1,80}」", line))


def _count_generic(text: str) -> int:
    return sum(1 for p in _GENERIC_PHRASES if p in text)


# ---------------------------------------------------------------------------
# Source text construction
# ---------------------------------------------------------------------------

def decide_source_text(card: Any) -> str:
    """Build source text from card fields for faithful extraction.

    Priority: title_plain > what_happened > technical_interpretation >
              why_important > evidence_lines > fact_check_confirmed >
              observation_metrics.
    Appends summary/description/full_text if still < MIN_CHARS_FOR_FAITHFUL.
    """
    parts: list[str] = []

    title = _clean(getattr(card, "title_plain", "") or "")
    if title:
        parts.append(title)

    for attr in ("what_happened", "technical_interpretation", "why_important"):
        val = _clean(getattr(card, attr, "") or "")
        if val and val not in parts:
            parts.append(val)

    for attr in ("evidence_lines", "fact_check_confirmed", "observation_metrics"):
        lst = getattr(card, attr, None) or []
        for item in lst[:5]:
            v = _clean(item or "")
            if v:
                parts.append(v)

    combined = " ".join(parts)

    # Fallback: append extra fields if still short
    if len(combined) < _MIN_CHARS_FOR_FAITHFUL:
        for attr in ("summary", "description", "full_text", "content"):
            val = _clean(getattr(card, attr, "") or "")
            if val and val not in combined:
                combined = combined + " " + val
            if len(combined) >= _MIN_CHARS_FOR_FAITHFUL:
                break

    return combined.strip()


# Backward-compat alias
def build_source_text(card: Any) -> str:
    return decide_source_text(card)


def is_english_dominant(text: str) -> bool:
    """True when zh_ratio < 0.35 (EN-dominant; relaxed from v5.1's 0.25)."""
    return _zh_ratio(text) < _ZH_DOMINANT_THRESHOLD


def should_apply_faithful(card: Any) -> bool:
    """True when source text is EN-dominant AND >= MIN_CHARS_FOR_FAITHFUL."""
    src = decide_source_text(card)
    return len(src) >= _MIN_CHARS_FOR_FAITHFUL and is_english_dominant(src)


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------

def extract_quote_tokens(sentence: str, anchors: list[str] | None = None) -> list[str]:
    """Return up to 2 verbatim quote tokens from sentence.

    Priority:
        1. Pre-extracted anchors that appear verbatim in sentence.
        2. Regex matches from _ANCHOR_KW.
    """
    found: list[str] = []

    for a in (anchors or []):
        if a and len(a) >= 2 and a in sentence and a not in found:
            found.append(a)
        if len(found) >= 2:
            return found

    for m in _ANCHOR_KW.finditer(sentence):
        tok = m.group(0).strip()
        if len(tok) >= 2 and tok not in found:
            found.append(tok)
        if len(found) >= 2:
            return found

    return found


def _tokens_from_src(src_text: str) -> list[str]:
    """Extract all anchor tokens present in source text (deduped, ordered)."""
    seen: list[str] = []
    for m in _ANCHOR_KW.finditer(src_text[:3000]):
        tok = m.group(0).strip()
        if len(tok) >= 2 and tok not in seen:
            seen.append(tok)
    return seen


def _impact_tokens_from_src(src_text: str) -> list[str]:
    """Tokens from impact-keyword sentences in source text."""
    tokens: list[str] = []
    for sent in _split_en_sents(src_text):
        if _IMPACT_KW.search(sent):
            for m in _ANCHOR_KW.finditer(sent):
                tok = m.group(0).strip()
                if len(tok) >= 2 and tok not in tokens:
                    tokens.append(tok)
    return tokens[:6]


# ---------------------------------------------------------------------------
# Token injection into Chinese sentences
# ---------------------------------------------------------------------------

def _inject_token(zh_sent: str, token: str, max_intro: int = 20) -> str:
    """Format: {ZH intro <=max_intro chars}：「{token}」（{ZH context}）。

    Splits zh_sent at first Chinese punctuation after >= 6 chars.
    Handles empty / too-short sentences gracefully.
    NEVER produces ellipsis.
    """
    zh_sent = _remove_ellipsis(zh_sent).strip()
    if not token:
        return zh_sent.rstrip("。") + "。" if zh_sent else "持續監控。"
    if not zh_sent:
        return f"確認事件：「{token}」。"

    # Find natural split point (first CJK punctuation after min 6 chars)
    intro_end = min(max_intro, len(zh_sent))
    for i, c in enumerate(zh_sent[: max_intro + 5]):
        if i >= 6 and c in "，。！？；：":
            intro_end = i
            break

    intro = zh_sent[:intro_end].rstrip("，。：！？；")
    rest = zh_sent[intro_end:].lstrip("，。：！？；").strip()
    rest = _remove_ellipsis(rest).rstrip("。")

    if rest and len(rest) >= 5:
        result = f"{intro}：「{token}」（{rest}）。"
    else:
        result = f"{intro}：「{token}」。"

    return _remove_ellipsis(result)


# ---------------------------------------------------------------------------
# Proof line
# ---------------------------------------------------------------------------

def _build_proof(card: Any) -> str:
    source_name = _clean(getattr(card, "source_name", "") or "") or "Unknown"
    date_str = ""
    for attr in ("published_at", "collected_at"):
        val = _clean(getattr(card, attr, "") or "")
        if val:
            date_str = val[:10]
            break
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"證據：來源：{source_name}（{date_str}）"


# ---------------------------------------------------------------------------
# Main generation function (rule-based, no LLM)
# ---------------------------------------------------------------------------

def generate_faithful_zh_v2(
    card: Any,
    q1_zh: str = "",
    q2_zh: str = "",
    q3_zh: list[str] | None = None,
    anchors: list[str] | None = None,
    source_text: str | None = None,
) -> dict | None:
    """Generate faithful ZH news with verbatim quote tokens (rule-based, no LLM).

    Parameters
    ----------
    card        : EduNewsCard or compatible object.
    q1_zh       : Chinese Q1 from newsroom rewrite (ZH base for intro/context).
    q2_zh       : Chinese Q2 from newsroom rewrite.
    q3_zh       : Chinese Q3 bullets from newsroom rewrite.
    anchors     : Pre-extracted anchor tokens from news_anchor module.
    source_text : EN source text (auto-decided if None).

    Returns
    -------
    dict with required fields, or None when card fails should_apply_faithful.
    NEVER produces ellipsis in output.
    """
    src = source_text if source_text is not None else decide_source_text(card)
    if len(src) < _MIN_CHARS_FOR_FAITHFUL or not is_english_dominant(src):
        return None

    # ── Collect all anchor tokens ────────────────────────────────────────────
    all_anchors: list[str] = list(anchors or [])

    # Direct regex extraction from source text (verbatim presence guaranteed)
    for tok in _tokens_from_src(src):
        if tok not in all_anchors:
            all_anchors.append(tok)

    # Fallback: news_anchor module extraction
    if not all_anchors:
        try:
            from utils.news_anchor import extract_anchors_from_card as _eafc
            _ar = _eafc(card)
            for a in _ar.get("anchors", []):
                if a and a not in all_anchors:
                    all_anchors.append(a)
        except Exception:
            pass

    anchors_top3 = all_anchors[:3]

    # ── Q1: 2 sentences, each with an anchor quote token ────────────────────
    q1_base = _split_zh_sents(q1_zh)
    if not q1_base:
        q1_base = ["相關事件已確認", "詳情待進一步分析"]
    while len(q1_base) < 2:
        q1_base.append("事件持續追蹤中")

    q1_lines: list[str] = []
    used_q1: list[str] = []
    for i in range(2):
        sent = _clean(q1_base[i] if i < len(q1_base) else q1_base[-1])
        # Pick first unused token present in source
        token: str | None = None
        for tok in all_anchors:
            if tok not in used_q1 and tok in src:
                token = tok
                used_q1.append(tok)
                break
        # Fallback: any anchor
        if token is None and all_anchors:
            token = all_anchors[min(i, len(all_anchors) - 1)]
        q1_lines.append(_inject_token(sent, token) if token else _clean(sent).rstrip("。") + "。")

    # ── Q2: 2 sentences, at least 1 impact-domain token ─────────────────────
    q2_base = _split_zh_sents(q2_zh)
    if not q2_base:
        q2_base = ["此事件影響待評估", "商業影響將持續追蹤"]
    while len(q2_base) < 2:
        q2_base.append("相關影響持續追蹤中")

    impact_toks = _impact_tokens_from_src(src)
    if not impact_toks:
        # Fallback: remaining unused anchors
        impact_toks = [a for a in all_anchors if a not in used_q1]
    if not impact_toks and all_anchors:
        impact_toks = all_anchors[:]

    q2_lines: list[str] = []
    used_q2: list[str] = []
    for i in range(2):
        sent = _clean(q2_base[i] if i < len(q2_base) else q2_base[-1])
        token = None
        for tok in impact_toks:
            if tok not in used_q2 and tok in src:
                token = tok
                used_q2.append(tok)
                break
        if token is None and impact_toks:
            token = impact_toks[min(i, len(impact_toks) - 1)]
        elif token is None and all_anchors:
            token = all_anchors[min(i, len(all_anchors) - 1)]
        q2_lines.append(_inject_token(sent, token) if token else _clean(sent).rstrip("。") + "。")

    # ── Q3: 3 bullets, each with a quote token reference ────────────────────
    q3_base = [_clean(b) for b in (q3_zh or []) if _clean(b) and len(_clean(b)) >= 12]
    while len(q3_base) < 3:
        q3_base.append("持續監控此事件後續發展（T+7）。")
    q3_base = q3_base[:3]

    q3_lines: list[str] = []
    q3_tok_pool = [a for a in all_anchors if a not in used_q1 and a not in used_q2]
    if not q3_tok_pool:
        q3_tok_pool = all_anchors[:]
    q3_tok_idx = 0

    for b in q3_base:
        b_clean = _remove_ellipsis(b).strip()
        if not _has_quote(b_clean):
            tok: str | None = None
            # Pick a token from pool (cycling)
            if q3_tok_pool:
                tok = q3_tok_pool[q3_tok_idx % len(q3_tok_pool)]
                q3_tok_idx += 1
            if tok and tok in src:
                b_stripped = b_clean.rstrip("。")
                b_clean = f"{b_stripped}（參考「{tok}」）。"
        q3_lines.append(_remove_ellipsis(b_clean))

    # ── Proof line ───────────────────────────────────────────────────────────
    proof_line = _build_proof(card)

    # ── Collect quote tokens in output ───────────────────────────────────────
    all_output = " ".join(q1_lines + q2_lines + q3_lines)
    qt_found: list[str] = re.findall(r"「([^」]{1,80})」", all_output)

    # ── Ellipsis audit (must be 0) ────────────────────────────────────────────
    ellipsis_hits = len(_ELLIPSIS_RE.findall(all_output))

    # ── ZH ratio ─────────────────────────────────────────────────────────────
    zh_rat = _zh_ratio(all_output)

    # ── Generic phrase count ──────────────────────────────────────────────────
    generic_hits = _count_generic(all_output)

    # ── Final sanitize (exec_sanitizer last-mile) ─────────────────────────────
    try:
        from utils.exec_sanitizer import sanitize_exec_text as _san
        q1_lines = [_san(l) for l in q1_lines]
        q2_lines = [_san(l) for l in q2_lines]
        q3_lines = [_san(l) for l in q3_lines]
    except Exception:
        pass

    return {
        "applied": True,
        "anchors_top3": anchors_top3,
        "q1": " ".join(q1_lines),
        "q2": " ".join(q2_lines),
        "q3_bullets": q3_lines,
        "proof_line": proof_line,
        "zh_ratio": zh_rat,
        "generic_hits": generic_hits,
        "anchor_missing": len(anchors_top3) == 0,
        "quote_tokens_found": qt_found,
        "ellipsis_hits": ellipsis_hits,
        "debug": {
            "source_len": len(src),
            "all_anchors": all_anchors[:5],
            "impact_tokens": impact_toks[:3],
        },
    }


# ---------------------------------------------------------------------------
# Backward-compat shim (old Ollama-based API)
# ---------------------------------------------------------------------------

def generate_faithful_zh(card: Any, source_text: str | None = None) -> dict | None:
    """Backward-compat shim — delegates to generate_faithful_zh_v2 (rule-based)."""
    return generate_faithful_zh_v2(card, source_text=source_text)


# ---------------------------------------------------------------------------
# Meta writer (Iteration 5.2 schema)
# ---------------------------------------------------------------------------

def write_faithful_zh_news_meta(
    results: list[dict],
    events_total: int = 0,
    applied_fail_count: int = 0,
    outdir: str | None = None,
) -> None:
    """Write outputs/faithful_zh_news.meta.json (Iteration 5.2 schema).

    Fields
    ------
    events_total          : Total cards that went through the pipeline.
    applied_count         : Cards where faithful was successfully applied.
    applied_fail_count    : Cards where should_apply_faithful was True but
                            generate_faithful_zh_v2 returned None.
    quote_present_count   : Applied cards whose output contains >= 1 「token」.
    quote_missing_count   : Applied cards with no quote tokens.
    quote_coverage_ratio  : quote_present_count / applied_count
                            (0.0 when applied_count = 0).
    ellipsis_hits_total   : Sum of ellipsis_hits across all applied results.
    sample_1              : First result's summary fields.
    """
    root = Path(outdir) if outdir else Path(__file__).resolve().parent.parent / "outputs"
    root.mkdir(parents=True, exist_ok=True)

    applied_count = len(results)
    total = max(events_total, applied_count)

    zh_ratios = [r.get("zh_ratio", 0.0) for r in results]
    avg_zh = round(sum(zh_ratios) / len(zh_ratios), 3) if zh_ratios else 0.0

    anchor_present = sum(1 for r in results if r.get("anchors_top3"))
    anchor_missing = sum(1 for r in results if r.get("anchor_missing"))
    anchor_coverage = round(anchor_present / applied_count, 3) if applied_count else 0.0

    quote_present = sum(1 for r in results if r.get("quote_tokens_found"))
    quote_missing = applied_count - quote_present
    # Gate metric: quote_present / applied_count (not events_total)
    quote_coverage = round(quote_present / applied_count, 3) if applied_count else 0.0

    ellipsis_total = sum(r.get("ellipsis_hits", 0) for r in results)
    generic_total = sum(r.get("generic_hits", 0) for r in results)

    sample_1: dict = {}
    if results:
        s = results[0]
        sample_1 = {
            "anchors_top3": s.get("anchors_top3", []),
            "q1": s.get("q1", ""),
            "q2": s.get("q2", ""),
            "proof": s.get("proof_line", ""),
            "quote_tokens_found": s.get("quote_tokens_found", []),
        }

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events_total": total,
        "applied_count": applied_count,
        "applied_fail_count": applied_fail_count,
        "avg_zh_ratio": avg_zh,
        "anchor_present_count": anchor_present,
        "anchor_missing_count": anchor_missing,
        "anchor_coverage_ratio": anchor_coverage,
        "quote_present_count": quote_present,
        "quote_missing_count": quote_missing,
        "quote_coverage_ratio": quote_coverage,
        "ellipsis_hits_total": ellipsis_total,
        "generic_phrase_hits_total": generic_total,
        "sample_1": sample_1,
    }

    out_path = root / "faithful_zh_news.meta.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
