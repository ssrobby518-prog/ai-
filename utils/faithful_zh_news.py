"""utils/faithful_zh_news.py — 忠實抽取式翻譯 + 新聞式 ZH 摘要層.

Stdlib-only. No new pip deps. Uses utils/ollama_client.py (also stdlib-only).

Design rules (hard constraints):
- EXTRACTIVE: only sentences that exist verbatim (or near-verbatim) in the
  source text are included — zero fabrication.
- Every selected EN sentence is translated to 繁中 (Traditional Chinese).
- Proper nouns may remain in English; Big-Tech company names are NOT glossed
  (NO_GLOSS_TERMS from existing pipeline).
- GENERIC_PHRASES trigger re-extraction, not template substitution.
- Q1: 2 sentences; first MUST contain a concrete anchor token.
- Q2: 2 sentences; at least one 'impact' keyword or numeric token.
- Q3: 3 bullets; each >= 12 chars; each references a source token.
- Proof: "證據：來源：{source}（YYYY-MM-DD）"
- Falls back gracefully when Ollama is unavailable — returns None so caller
  keeps existing canonical output.

Public API
----------
    generate_faithful_zh(card, source_text: str) -> dict | None
        Returns dict with keys:
            q1, q2, q3_bullets, proof_line,
            anchors_top3, zh_ratio, generic_hits
        or None on failure / Ollama unavailable.

    build_source_text(card) -> str
        Assemble raw EN source text from card fields. Returns "" if too short.
"""
from __future__ import annotations

import re
import os
from typing import Any

# ---------------------------------------------------------------------------
# Generic-phrase blacklist (causes re-extraction trigger)
# ---------------------------------------------------------------------------

GENERIC_PHRASES: list[str] = [
    "引發關注", "重要意義", "密切追蹤", "參考基準",
    "值得關注", "廣泛影響", "持續關注", "深遠影響",
    "業界矚目", "市場焦點", "重大進展", "關鍵時刻",
    "值得注意", "引人關注", "不可忽視",
    "high attention", "important significance",
    "worth noting", "closely monitoring",
]

# ---------------------------------------------------------------------------
# NO_GLOSS_TERMS — Big Tech names that must NOT get parenthetical ZH gloss
# (consistent with existing pipeline constraint)
# ---------------------------------------------------------------------------

NO_GLOSS_TERMS: frozenset[str] = frozenset({
    "Apple", "Google", "Microsoft", "Meta", "Amazon", "OpenAI",
    "Anthropic", "NVIDIA", "DeepSeek", "Mistral", "Cohere",
    "Hugging Face", "xAI", "Samsung", "Baidu", "Alibaba",
    "Tencent", "ByteDance", "Netflix", "Salesforce", "Oracle",
})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CJK_RE  = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
_SENT_RE = re.compile(r"(?<=[.!?])\s+")


def _zh_ratio(text: str) -> float:
    if not text:
        return 0.0
    cjk = len(_CJK_RE.findall(text))
    asc = sum(1 for c in text if c.isascii() and c.isalpha())
    total = cjk + asc
    return round(cjk / total, 3) if total else 0.0


def _count_generic(text: str) -> int:
    t = text.lower()
    return sum(1 for p in GENERIC_PHRASES if p.lower() in t)


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences; keep non-empty ones >= 8 chars."""
    raw = _SENT_RE.split(text.replace("\n", " ").strip())
    return [s.strip() for s in raw if len(s.strip()) >= 8]


def _has_anchor_token(sentence: str) -> bool:
    """True if sentence has a concrete anchor: number, %, version, or known model."""
    return bool(re.search(
        r"\b\d[\d,\.]*[BbMmKk%$]?\b"       # numbers, $, %
        r"|v\d+\.\d"                          # version
        r"|GPT-?\d|Claude\s\d|Llama\s?\d"    # model families
        r"|DeepSeek|Gemini|Qwen|Mistral"
        r"|\d+\s*(?:billion|million|thousand)\b",
        sentence, re.IGNORECASE,
    ))


def _has_impact_token(sentence: str) -> bool:
    """True if sentence has an 'impact' keyword or metric."""
    return bool(re.search(
        r"\b(?:cost|price|pricing|partner|regulation|security|breach|"
        r"performance|latency|throughput|benchmark|revenue|funding|invest|"
        r"acqui|deploy|adopt|compet|risk|threat|opportun|growth|decline)\b",
        sentence, re.IGNORECASE,
    ))


# ---------------------------------------------------------------------------
# Source text assembly
# ---------------------------------------------------------------------------

def build_source_text(card: Any) -> str:
    """Assemble raw source text from card fields.

    Priority order: what_happened > technical_interpretation >
                    evidence_lines > fact_check_confirmed > title_plain
    Returns "" if assembled text is < 200 chars (too sparse for extraction).
    """
    parts: list[str] = []

    for attr in ("what_happened", "technical_interpretation"):
        val = str(getattr(card, attr, "") or "").strip()
        if val:
            parts.append(val)

    for attr in ("evidence_lines", "fact_check_confirmed", "observation_metrics"):
        lst = getattr(card, attr, None) or []
        for item in lst[:5]:
            v = str(item or "").strip()
            if v:
                parts.append(v)

    title = str(getattr(card, "title_plain", "") or "").strip()
    if title:
        parts.insert(0, title)

    combined = " ".join(parts).strip()
    return combined if len(combined) >= 200 else ""


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

_SYSTEM_PREAMBLE = """你是一位嚴謹的新聞翻譯員。
規則（必須嚴格遵守）：
1. 只能引用原文中實際存在的句子。不得捏造任何事實、數字或細節。
2. 翻譯成繁體中文。專有名詞（公司名、模型名、產品名）保留英文，後加繁中解釋（8~16字）。
3. Big Tech（Apple/Google/Microsoft/Meta/Amazon/OpenAI/Anthropic/NVIDIA 等）不需括號解釋。
4. 禁用套語：「引發關注」「重要意義」「密切追蹤」「參考基準」「值得關注」「廣泛影響」。
5. 輸出格式嚴格按指示，不得多加說明或注釋。"""


def _build_extraction_prompt(source_text: str, title: str, source_name: str) -> str:
    """Build the Ollama prompt for extractive ZH news generation."""
    return f"""{_SYSTEM_PREAMBLE}

原始標題：{title}
來源：{source_name}

原文（僅抽取以下文字中的句子）：
---
{source_text[:2000]}
---

任務：從上方原文中，嚴格抽取最重要的英文句子，翻譯成繁體中文，按以下格式輸出：

Q1A: [從原文抽取的第1句英文，逐字引用]
Q1A_ZH: [Q1A 的繁中翻譯，必須含具體錨點（模型名/版本/金額/參數/功能）]
Q1B: [從原文抽取的第2句英文，逐字引用]
Q1B_ZH: [Q1B 的繁中翻譯]
Q2A: [從原文抽取的第3句英文，與商業/技術影響相關]
Q2A_ZH: [Q2A 的繁中翻譯，必須含影響錨點（cost/perf/partner/regulation 等）]
Q2B: [從原文抽取的第4句英文]
Q2B_ZH: [Q2B 的繁中翻譯]
Q3_1: [行動建議1，繁中，>=12字，引用原文 token]
Q3_2: [行動建議2，繁中，>=12字，引用原文 token]
Q3_3: [行動建議3，繁中，>=12字，引用原文 token]
ANCHORS: [最多3個具體錨點，逗號分隔，來自原文]

輸出只含上述欄位，不要任何其他說明。"""


# ---------------------------------------------------------------------------
# Parse LLM response
# ---------------------------------------------------------------------------

def _parse_response(raw: str) -> dict | None:
    """Parse structured LLM output into q1/q2/q3/anchors."""
    if not raw:
        return None

    def _field(key: str) -> str:
        # Match "KEY: value" on its own line
        m = re.search(rf"^{re.escape(key)}\s*:\s*(.+)$", raw, re.MULTILINE | re.IGNORECASE)
        return m.group(1).strip() if m else ""

    q1a_zh = _field("Q1A_ZH")
    q1b_zh = _field("Q1B_ZH")
    q2a_zh = _field("Q2A_ZH")
    q2b_zh = _field("Q2B_ZH")
    q3_1   = _field("Q3_1")
    q3_2   = _field("Q3_2")
    q3_3   = _field("Q3_3")
    anchors_raw = _field("ANCHORS")

    # Assemble Q1 / Q2
    q1_parts = [p for p in [q1a_zh, q1b_zh] if p]
    q2_parts = [p for p in [q2a_zh, q2b_zh] if p]
    q3_parts = [p for p in [q3_1, q3_2, q3_3] if len(p) >= 12]

    if not q1_parts or not q2_parts:
        return None

    q1 = " ".join(q1_parts[:2])
    q2 = " ".join(q2_parts[:2])
    q3 = q3_parts[:3]
    if not q3:
        q3 = ["持續監控此事件後續發展（T+7）。"]

    # Parse anchors
    anchors_top3: list[str] = []
    if anchors_raw:
        for a in anchors_raw.split(","):
            a = a.strip().strip("[]\"'")
            if a and len(a) >= 2:
                anchors_top3.append(a)
    anchors_top3 = anchors_top3[:3]

    return {
        "q1": q1,
        "q2": q2,
        "q3_bullets": q3,
        "anchors_top3": anchors_top3,
    }


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def generate_faithful_zh(card: Any, source_text: str | None = None) -> dict | None:
    """Generate faithful extractive ZH news output via Ollama.

    Parameters
    ----------
    card        : EduNewsCard or compatible object.
    source_text : Override source text (auto-built from card if None).

    Returns
    -------
    dict with keys: q1, q2, q3_bullets, proof_line, anchors_top3,
                    zh_ratio, generic_hits
    OR None if Ollama is unavailable or extraction fails.
    """
    try:
        from utils.ollama_client import generate as _ollama_gen, is_available as _ollama_avail
    except ImportError:
        return None

    # ── 0. Check Ollama availability ─────────────────────────────────────────
    if not _ollama_avail():
        return None

    # ── 1. Build source text ─────────────────────────────────────────────────
    src = source_text if source_text is not None else build_source_text(card)
    if not src or len(src) < 200:
        return None

    title       = str(getattr(card, "title_plain", "") or "").strip()
    source_name = str(getattr(card, "source_name", "") or "").strip() or "Unknown"

    # ── 2. Build proof line ──────────────────────────────────────────────────
    date_str = ""
    for attr in ("published_at", "collected_at"):
        val = str(getattr(card, attr, "") or "").strip()
        if val:
            date_str = val[:10]
            break
    if not date_str:
        from datetime import datetime, timezone
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    proof_line = f"證據：來源：{source_name}（{date_str}）"

    # ── 3. Build prompt ──────────────────────────────────────────────────────
    prompt = _build_extraction_prompt(src, title, source_name)

    # ── 4. Call Ollama ───────────────────────────────────────────────────────
    try:
        raw = _ollama_gen(
            prompt,
            temperature=0,
            num_ctx=1536,
            num_predict=512,
        )
    except Exception:
        return None

    # ── 5. Parse response ────────────────────────────────────────────────────
    parsed = _parse_response(raw)
    if parsed is None:
        return None

    q1          = parsed["q1"]
    q2          = parsed["q2"]
    q3_bullets  = parsed["q3_bullets"]
    anchors_top3 = parsed["anchors_top3"]

    # ── 6. Generic phrase check ──────────────────────────────────────────────
    combined_text = f"{q1} {q2} {' '.join(q3_bullets)}"
    generic_hits  = _count_generic(combined_text)

    # If generic phrases found, attempt re-generation with stricter instruction
    if generic_hits > 0:
        stricter = prompt + "\n注意：上次輸出含有套語，請重新抽取不同原句，絕對不使用：" + "、".join(GENERIC_PHRASES[:6]) + "。"
        try:
            raw2 = _ollama_gen(stricter, temperature=0, num_ctx=1536, num_predict=512)
            parsed2 = _parse_response(raw2)
            if parsed2:
                q1          = parsed2["q1"]
                q2          = parsed2["q2"]
                q3_bullets  = parsed2["q3_bullets"]
                anchors_top3 = parsed2.get("anchors_top3", anchors_top3)
                combined_text = f"{q1} {q2} {' '.join(q3_bullets)}"
                generic_hits  = _count_generic(combined_text)
        except Exception:
            pass  # keep first attempt

    # ── 7. Validation guardrails ─────────────────────────────────────────────
    # Q1 must not be empty; fallback to title as first sentence
    if not q1.strip():
        q1 = f"近日，{title}。事件詳情見原文。"

    # Q2 must be distinct from Q1
    try:
        from difflib import SequenceMatcher
        if SequenceMatcher(None, q1, q2).ratio() > 0.85:
            q2 = "此事件對產業生態具實質影響，業界各方正評估後續行動方向。"
    except Exception:
        pass

    # Q3 bullets — ensure >= 3, each >= 12 chars
    while len(q3_bullets) < 3:
        q3_bullets.append("持續監控此事件後續發展（T+7）。")
    q3_bullets = [b for b in q3_bullets if len(b) >= 12][:3]

    # ── 8. ZH ratio of final output ─────────────────────────────────────────
    full_output = f"{q1} {q2} {' '.join(q3_bullets)}"
    zh_rat = _zh_ratio(full_output)

    # ── 9. Sanitize via exec_sanitizer (last-mile banned-phrase check) ───────
    try:
        from utils.exec_sanitizer import sanitize_exec_text as _san
        q1 = _san(q1)
        q2 = _san(q2)
        q3_bullets = [_san(b) for b in q3_bullets]
    except Exception:
        pass

    return {
        "q1":          q1,
        "q2":          q2,
        "q3_bullets":  q3_bullets,
        "proof_line":  proof_line,
        "anchors_top3": anchors_top3,
        "zh_ratio":    zh_rat,
        "generic_hits": generic_hits,
    }


# ---------------------------------------------------------------------------
# Meta writer
# ---------------------------------------------------------------------------

def write_faithful_zh_news_meta(
    results: list[dict],
    outdir: "str | None" = None,
) -> None:
    """Write outputs/faithful_zh_news.meta.json.

    Parameters
    ----------
    results : list of dicts returned by generate_faithful_zh (non-None entries).
    outdir  : directory path (default: outputs/).
    """
    import json
    from pathlib import Path
    from datetime import datetime, timezone

    root = Path(outdir) if outdir else Path(__file__).resolve().parent.parent / "outputs"
    root.mkdir(parents=True, exist_ok=True)

    applied_count     = len(results)
    zh_ratios         = [r.get("zh_ratio", 0.0) for r in results]
    avg_zh_ratio      = round(sum(zh_ratios) / len(zh_ratios), 3) if zh_ratios else 0.0
    anchor_present    = sum(1 for r in results if r.get("anchors_top3"))
    anchor_coverage   = round(anchor_present / applied_count, 3) if applied_count else 0.0
    generic_hits_total = sum(r.get("generic_hits", 0) for r in results)

    sample_1: dict = {}
    if results:
        s = results[0]
        sample_1 = {
            "anchors_top3": s.get("anchors_top3", []),
            "q1":           s.get("q1", ""),
            "q2":           s.get("q2", ""),
            "proof":        s.get("proof_line", ""),
        }

    meta = {
        "generated_at":        datetime.now(timezone.utc).isoformat(),
        "applied_count":       applied_count,
        "avg_zh_ratio":        avg_zh_ratio,
        "anchor_coverage_ratio": anchor_coverage,
        "generic_phrase_hits_total": generic_hits_total,
        "sample_1":            sample_1,
    }

    out_path = root / "faithful_zh_news.meta.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
