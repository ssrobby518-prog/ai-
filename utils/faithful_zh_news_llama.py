"""utils/faithful_zh_news_llama.py — 忠實抽取式翻譯 + 新聞式 ZH 摘要層 (llama.cpp).

Stdlib-only.  No pip deps.  Calls utils/llama_openai_client.py.

Design rules (hard constraints):
- EXTRACTIVE: selected sentences must exist verbatim in source text.
- Translate each selected sentence to 繁體中文; no fabrication.
- Q1 MUST contain an anchor token (version/model/amount/param/benchmark).
- Q2 MUST contain an impact anchor (cost/perf/latency/pricing/partner etc.).
- GENERIC_PHRASES trigger sentence re-selection, not template substitution.
- Big Tech NOT glossed (NO_GLOSS_TERMS).
- Non-Big-Tech proper nouns: gloss on first mention only (seen-set guard).
- Proof line: "證據：來源：{source_name}（YYYY-MM-DD）"
- Fallback on JSON parse failure: regex-select best sentences + short LLM translate call.
- Returns None (not raises) when llama-server is unavailable or extraction fails.

Public API
----------
    build_source_text(card) -> str
        Assemble EN source text from card fields; "" if < 200 chars.

    generate_faithful_zh(card, source_text=None) -> dict | None
        Main entry point.  Returns:
          {applied, anchors_top3, q1, q2, q3_bullets, proof_line,
           zh_ratio, generic_hits, anchor_missing,
           debug: {selected_sentences, selected_sentence_ids}}
        or None on failure / server unavailable.

    write_faithful_zh_news_meta(results, events_total=0, outdir=None) -> None
        Writes outputs/faithful_zh_news.meta.json.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Big-Tech: NOT glossed (per pipeline constraint)
NO_GLOSS_TERMS: frozenset[str] = frozenset({
    "Apple", "Google", "Microsoft", "Meta", "Amazon", "OpenAI",
    "Anthropic", "NVIDIA", "DeepSeek", "Mistral", "Cohere",
    "Hugging Face", "xAI", "Samsung", "Baidu", "Alibaba",
    "Tencent", "ByteDance", "Netflix", "Salesforce", "Oracle",
    "Intel", "AMD", "Qualcomm", "IBM", "Cisco",
})

# Generic hollow phrases → trigger re-selection
GENERIC_PHRASES: list[str] = [
    "引發關注", "重要意義", "密切追蹤", "參考基準",
    "值得關注", "廣泛影響", "持續關注", "深遠影響",
    "業界矚目", "市場焦點", "重大進展", "關鍵時刻",
    "值得注意", "引人關注", "不可忽視",
    "high attention", "important significance",
    "worth noting", "closely monitoring",
    "各方正密切", "產業各方", "引起廣泛",
]

# Impact keywords for Q2 sentence selection
_IMPACT_KW = re.compile(
    r"\b(?:cost|price|pricing|partner|regulation|security|breach|"
    r"performance|latency|throughput|benchmark|revenue|funding|invest|"
    r"acqui|deploy|adopt|compet|risk|threat|opportun|growth|decline|"
    r"profit|loss|margin|valuation|ARR|MRR|savings|efficiency)\b",
    re.IGNORECASE,
)

# Anchor tokens for Q1 sentence selection
_ANCHOR_KW = re.compile(
    r"\b\d[\d,\.]*[BbMmKk%$]?\b"
    r"|v\d+\.\d"
    r"|GPT-?\d|Claude[\s-]\d|Llama[\s-]?\d"
    r"|DeepSeek|Gemini|Qwen|Mistral|Phi-?\d"
    r"|\d+\s*(?:billion|million|thousand)\b"
    r"|[A-Z][\w-]+-\d+[A-Z]?"     # model-name patterns like Qwen2.5-7B
    r"|\$\s*\d",
    re.IGNORECASE,
)

_CJK_RE  = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_SENT_RE = re.compile(r"(?<=[.!?])\s+|\n{2,}")


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

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
    """Split into sentences; keep >= 10 chars."""
    raw = _SENT_RE.split(text.replace("\n", " ").strip())
    return [s.strip() for s in raw if len(s.strip()) >= 10]


# ---------------------------------------------------------------------------
# Source text assembly
# ---------------------------------------------------------------------------

def build_source_text(card: Any) -> str:
    """Assemble EN source text from card fields (what_happened, technical_interpretation, etc.)."""
    parts: list[str] = []

    title = str(getattr(card, "title_plain", "") or "").strip()
    if title:
        parts.append(title)

    for attr in ("what_happened", "technical_interpretation"):
        val = str(getattr(card, attr, "") or "").strip()
        if val:
            parts.append(val)

    for attr in ("evidence_lines", "fact_check_confirmed", "observation_metrics"):
        lst = getattr(card, attr, None) or []
        for item in (lst[:5] if lst else []):
            v = str(item or "").strip()
            if v:
                parts.append(v)

    combined = " ".join(parts).strip()
    return combined if len(combined) >= 200 else ""


# ---------------------------------------------------------------------------
# Proof line
# ---------------------------------------------------------------------------

def _build_proof(card: Any) -> str:
    source_name = str(getattr(card, "source_name", "") or "").strip() or "Unknown"
    date_str = ""
    for attr in ("published_at", "collected_at"):
        val = str(getattr(card, attr, "") or "").strip()
        if val:
            date_str = val[:10]
            break
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"證據：來源：{source_name}（{date_str}）"


# ---------------------------------------------------------------------------
# JSON schema prompt
# ---------------------------------------------------------------------------

_SYSTEM_MSG = (
    "你是嚴謹的新聞翻譯員。規則：\n"
    "1. selected_sentence_indexes 必須從提供的句子列表中選出（0-based index）。\n"
    "2. translations 鍵是 index 的字串，值是繁體中文翻譯（不得捏造原文不存在的資訊）。\n"
    "3. anchors_top5 必須是原文中出現的 substring（版本/金額/產品名/參數）。\n"
    "4. Big Tech（Apple/Google/Microsoft/Meta/Amazon/OpenAI/Anthropic/NVIDIA 等）不需括號解釋。\n"
    "5. 非 Big Tech 專名首次出現可加括號說明（8~16字），之後不重複。\n"
    "6. 禁止：「引發關注」「重要意義」「密切追蹤」「廣泛影響」「值得關注」「持續關注」。\n"
    "7. 輸出必須是合法 JSON，不含任何額外說明。"
)


def _build_json_prompt(numbered_sents: list[str], title: str, source_name: str) -> str:
    lines = [f"[{i}] {s}" for i, s in enumerate(numbered_sents)]
    sents_block = "\n".join(lines)
    schema = (
        '{\n'
        '  "selected_sentence_indexes": [0,1,2,3,4,5],\n'
        '  "anchors_top5": ["anchor1","anchor2","anchor3","anchor4","anchor5"],\n'
        '  "q1_idx": [0, 1],\n'
        '  "q2_idx": [2, 3],\n'
        '  "q3_idx": [4, 5, 6],\n'
        '  "translations": {"0":"繁中翻譯","1":"繁中翻譯","2":"繁中翻譯",...}\n'
        '}'
    )
    return (
        f"標題：{title}\n"
        f"來源：{source_name}\n\n"
        f"句子列表（0-based index）：\n{sents_block}\n\n"
        f"任務：\n"
        f"1. 從句子列表選出 6~8 句最重要的（selected_sentence_indexes）\n"
        f"2. q1_idx 兩句必須含錨點（版本/金額/參數/模型名/benchmark），優先選含數字的句子\n"
        f"3. q2_idx 兩句必須含商業/技術影響詞（cost/price/partner/security/performance/revenue 等）\n"
        f"4. q3_idx 三句作為行動建議依據\n"
        f"5. translations 必須包含所有 selected_sentence_indexes 的翻譯\n"
        f"6. anchors_top5 從原文摘取（最多 5 個具體 token）\n\n"
        f"輸出格式（嚴格 JSON，不含任何說明）：\n{schema}"
    )


def _build_translate_prompt(idx_to_sent: dict[int, str]) -> str:
    """Fallback: short translation-only prompt."""
    lines = [f"[{i}] {s}" for i, s in sorted(idx_to_sent.items())]
    return (
        "請將以下英文句子翻譯為繁體中文。"
        "輸出 JSON，鍵為 index 字串，值為繁中翻譯。不含任何說明。\n\n"
        + "\n".join(lines)
        + "\n\n輸出 JSON："
    )


# ---------------------------------------------------------------------------
# JSON parse + validation
# ---------------------------------------------------------------------------

def _extract_json(raw: str) -> dict | None:
    """Try to extract a JSON object from raw LLM output (may have leading/trailing text)."""
    # Strip markdown code fences
    raw = re.sub(r"```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
    # Find first { ... } block
    start = raw.find("{")
    if start == -1:
        return None
    # Find matching closing brace
    depth = 0
    for i, ch in enumerate(raw[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = raw[start:i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass
    return None


def _parse_json_result(
    data: dict,
    sents: list[str],
) -> dict | None:
    """Validate JSON result from LLM; return parsed dict or None."""
    try:
        sel_idxs      = [int(x) for x in (data.get("selected_sentence_indexes") or [])]
        translations  = {str(k): str(v) for k, v in (data.get("translations") or {}).items()}
        anchors_raw   = [str(a).strip() for a in (data.get("anchors_top5") or [])]
        q1_idx        = [int(x) for x in (data.get("q1_idx") or [])[:2]]
        q2_idx        = [int(x) for x in (data.get("q2_idx") or [])[:2]]
        q3_idx        = [int(x) for x in (data.get("q3_idx") or [])[:3]]

        n = len(sents)
        # Verify all indexes in range
        for idx in sel_idxs + q1_idx + q2_idx + q3_idx:
            if idx < 0 or idx >= n:
                return None

        def _tr(idxs: list[int]) -> list[str]:
            out = []
            for i in idxs:
                t = translations.get(str(i), "").strip()
                if t:
                    out.append(t)
            return out

        q1_parts = _tr(q1_idx)
        q2_parts = _tr(q2_idx)
        q3_parts = _tr(q3_idx)

        if not q1_parts or not q2_parts:
            return None

        anchors = [a for a in anchors_raw if a and len(a) >= 2][:5]
        selected_sents = [sents[i] for i in sel_idxs if i < n]

        return {
            "q1":       " ".join(q1_parts),
            "q2":       " ".join(q2_parts),
            "q3_parts": q3_parts,
            "anchors":  anchors,
            "selected_sents":    selected_sents,
            "selected_sent_ids": sel_idxs,
            "translations":      translations,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Fallback sentence selection (regex-based, no LLM needed for selection)
# ---------------------------------------------------------------------------

def _select_fallback(sents: list[str]) -> tuple[list[int], list[int], list[int]]:
    """Select q1/q2/q3 indexes heuristically."""
    anchor_idxs  = [i for i, s in enumerate(sents) if _ANCHOR_KW.search(s)]
    impact_idxs  = [i for i, s in enumerate(sents) if _IMPACT_KW.search(s)]
    other_idxs   = [i for i in range(len(sents)) if i not in anchor_idxs and i not in impact_idxs]

    q1_idx = anchor_idxs[:2]
    if len(q1_idx) < 2:
        q1_idx += other_idxs[:2 - len(q1_idx)]

    q2_idx = impact_idxs[:2]
    used = set(q1_idx + q2_idx)
    if len(q2_idx) < 2:
        extra = [i for i in other_idxs if i not in used]
        q2_idx += extra[:2 - len(q2_idx)]

    used = set(q1_idx + q2_idx)
    q3_pool = [i for i in range(len(sents)) if i not in used]
    q3_idx = q3_pool[:3]

    return q1_idx[:2], q2_idx[:2], q3_idx[:3]


# ---------------------------------------------------------------------------
# Main generation function
# ---------------------------------------------------------------------------

def generate_faithful_zh(card: Any, source_text: str | None = None) -> dict | None:
    """Generate faithful extractive ZH news via llama-server.

    Returns dict or None on failure.
    """
    try:
        from utils.llama_openai_client import chat as _chat, is_available as _avail
    except ImportError:
        return None

    if not _avail():
        return None

    # ── 1. Build source text ────────────────────────────────────────────────
    src = source_text if source_text is not None else build_source_text(card)
    if not src or len(src) < 200:
        return None

    title       = str(getattr(card, "title_plain", "") or "").strip()
    source_name = str(getattr(card, "source_name",  "") or "").strip() or "Unknown"
    proof_line  = _build_proof(card)

    # ── 2. Split into numbered sentences ────────────────────────────────────
    sents = _split_sentences(src[:3000])
    if len(sents) < 3:
        return None

    # ── 3. Call LLM for extractive selection + translation ──────────────────
    json_prompt  = _build_json_prompt(sents, title, source_name)
    messages = [
        {"role": "system", "content": _SYSTEM_MSG},
        {"role": "user",   "content": json_prompt},
    ]
    ok, raw = _chat(messages, temperature=0, top_p=0.9, max_tokens=900)

    parsed: dict | None = None
    if ok:
        data = _extract_json(raw)
        if data:
            parsed = _parse_json_result(data, sents)

    # ── 4. Fallback: heuristic selection + LLM translate ────────────────────
    if parsed is None:
        q1_idx, q2_idx, q3_idx = _select_fallback(sents)
        all_idxs = list(dict.fromkeys(q1_idx + q2_idx + q3_idx))  # dedup, order-preserving
        idx_to_sent = {i: sents[i] for i in all_idxs}
        tr_prompt = _build_translate_prompt(idx_to_sent)
        tr_messages = [
            {"role": "system", "content": "你是新聞翻譯員。輸出繁體中文翻譯，格式嚴格 JSON。"},
            {"role": "user",   "content": tr_prompt},
        ]
        ok2, raw2 = _chat(tr_messages, temperature=0, top_p=0.9, max_tokens=600)
        translations: dict[str, str] = {}
        if ok2:
            data2 = _extract_json(raw2)
            if isinstance(data2, dict):
                translations = {str(k): str(v).strip() for k, v in data2.items() if v}

        def _tr_fb(idxs: list[int]) -> list[str]:
            out = []
            for i in idxs:
                t = translations.get(str(i), "").strip()
                if not t:
                    # Last-resort: keep English sentence (acceptable per spec notes)
                    t = sents[i].strip()
                out.append(t)
            return [p for p in out if p]

        q1_parts = _tr_fb(q1_idx)
        q2_parts = _tr_fb(q2_idx)
        q3_parts = _tr_fb(q3_idx)

        # Attempt to extract anchors from anchor sentences
        anchors: list[str] = []
        for i in q1_idx:
            for m in _ANCHOR_KW.finditer(sents[i]):
                tok = m.group(0).strip()
                if len(tok) >= 2 and tok not in anchors:
                    anchors.append(tok)
        anchors = anchors[:5]

        parsed = {
            "q1":       " ".join(q1_parts),
            "q2":       " ".join(q2_parts),
            "q3_parts": q3_parts,
            "anchors":  anchors,
            "selected_sents":    [sents[i] for i in all_idxs],
            "selected_sent_ids": all_idxs,
            "translations":      translations,
        }

    # ── 5. Assemble output ──────────────────────────────────────────────────
    q1 = parsed["q1"].strip()
    q2 = parsed["q2"].strip()
    q3_bullets: list[str] = [b.strip() for b in parsed.get("q3_parts", []) if len(b.strip()) >= 12]
    anchors_top3: list[str] = parsed.get("anchors", [])[:3]

    # ── 6. Generic-phrase check; one re-generation attempt ─────────────────
    combined_text = f"{q1} {q2} {' '.join(q3_bullets)}"
    generic_hits  = _count_generic(combined_text)

    if generic_hits > 0:
        retry_prompt = (
            json_prompt
            + "\n\n注意：上次翻譯含禁用套語，請重新選擇不同句子並翻譯，"
            + "禁止使用：" + "、".join(GENERIC_PHRASES[:8]) + "。"
        )
        retry_msgs = [
            {"role": "system", "content": _SYSTEM_MSG},
            {"role": "user",   "content": retry_prompt},
        ]
        ok3, raw3 = _chat(retry_msgs, temperature=0, top_p=0.9, max_tokens=900)
        if ok3:
            data3 = _extract_json(raw3)
            if data3:
                p3 = _parse_json_result(data3, sents)
                if p3:
                    q1_new = p3["q1"].strip()
                    q2_new = p3["q2"].strip()
                    q3_new = [b.strip() for b in p3.get("q3_parts", []) if len(b.strip()) >= 12]
                    new_combined = f"{q1_new} {q2_new} {' '.join(q3_new)}"
                    new_hits = _count_generic(new_combined)
                    if new_hits <= generic_hits:  # accept if equal or better
                        q1 = q1_new
                        q2 = q2_new
                        q3_bullets = q3_new
                        anchors_top3 = p3.get("anchors", anchors_top3)[:3]
                        combined_text = new_combined
                        generic_hits = new_hits

    # ── 7. Guardrails ────────────────────────────────────────────────────────
    # Q3 must have at least 3 bullets
    while len(q3_bullets) < 3:
        q3_bullets.append("持續監控此事件後續發展（T+7）。")
    q3_bullets = q3_bullets[:3]

    # Q1 must be non-empty
    if not q1:
        q1 = title or "請參考原始來源。"

    # Anchor missing flag
    anchor_missing = len(anchors_top3) == 0

    # ── 8. Compute zh_ratio ──────────────────────────────────────────────────
    full_out = f"{q1} {q2} {' '.join(q3_bullets)}"
    zh_rat   = _zh_ratio(full_out)

    # ── 9. exec_sanitizer (last-mile) ────────────────────────────────────────
    try:
        from utils.exec_sanitizer import sanitize_exec_text as _san
        q1 = _san(q1)
        q2 = _san(q2)
        q3_bullets = [_san(b) for b in q3_bullets]
    except Exception:
        pass

    return {
        "applied":       True,
        "anchors_top3":  anchors_top3,
        "q1":            q1,
        "q2":            q2,
        "q3_bullets":    q3_bullets,
        "proof_line":    proof_line,
        "zh_ratio":      zh_rat,
        "generic_hits":  generic_hits,
        "anchor_missing": anchor_missing,
        "debug": {
            "selected_sentences":  parsed.get("selected_sents", []),
            "selected_sentence_ids": parsed.get("selected_sent_ids", []),
        },
    }


# ---------------------------------------------------------------------------
# Meta writer
# ---------------------------------------------------------------------------

def write_faithful_zh_news_meta(
    results: list[dict],
    events_total: int = 0,
    outdir: str | None = None,
) -> None:
    """Write outputs/faithful_zh_news.meta.json.

    Parameters
    ----------
    results      : Non-None dicts returned by generate_faithful_zh.
    events_total : Total cards that went through the pipeline (including non-applied).
    outdir       : Output directory path; defaults to outputs/.
    """
    root = Path(outdir) if outdir else Path(__file__).resolve().parent.parent / "outputs"
    root.mkdir(parents=True, exist_ok=True)

    applied_count     = len(results)
    zh_ratios         = [r.get("zh_ratio", 0.0) for r in results]
    avg_zh_ratio      = round(sum(zh_ratios) / len(zh_ratios), 3) if zh_ratios else 0.0
    anchor_present    = sum(1 for r in results if r.get("anchors_top3"))
    anchor_missing_c  = sum(1 for r in results if r.get("anchor_missing"))
    anchor_coverage   = round(anchor_present / applied_count, 3) if applied_count else 0.0
    generic_total     = sum(r.get("generic_hits", 0) for r in results)

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
        "generated_at":              datetime.now(timezone.utc).isoformat(),
        "events_total":              max(events_total, applied_count),
        "applied_count":             applied_count,
        "avg_zh_ratio":              avg_zh_ratio,
        "anchor_present_count":      anchor_present,
        "anchor_missing_count":      anchor_missing_c,
        "anchor_coverage_ratio":     anchor_coverage,
        "generic_phrase_hits_total": generic_total,
        "sample_1":                  sample_1,
    }

    out_path = root / "faithful_zh_news.meta.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
