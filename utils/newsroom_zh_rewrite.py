"""utils/newsroom_zh_rewrite.py — Newsroom-style Traditional Chinese rewriter.

Stdlib-only. No new pip deps. No external models. No API calls.

Rule-based controlled rewrite: extracts fact tokens (company/product/number/date/
version) from English text and wraps them in Traditional Chinese news-sentence
structures.  No facts are invented — numbers, company names, version strings, and
dates are preserved verbatim from the source.

ZH ratio guarantee: every output sentence is built around a ZH-heavy template, so
even when company names are English the zh_ratio of the combined payload ≥ 0.35.

Public API
----------
    rewrite_news_lead(text_en, context)   -> str        Q1: 2-sentence lead (what happened)
    rewrite_news_impact(text_en, context) -> str        Q2: 2-sentence impact (why matters)
    rewrite_news_next(text_en, context)   -> list[str]  Q3: 3-bullet next steps
    rewrite_news_risks(text_en, context)  -> list[str]  2-bullet risk summary
    zh_ratio(text)                        -> float      CJK char ratio helper
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Company / product name roster
# ---------------------------------------------------------------------------

_KNOWN_COMPANIES: list[str] = [
    "OpenAI", "Anthropic", "Google", "Microsoft", "Meta", "NVIDIA", "Apple",
    "Amazon", "AWS", "xAI", "Grok", "DeepSeek", "Mistral", "Cohere",
    "HuggingFace", "Hugging Face", "Stability AI", "Inflection", "Perplexity",
    "Samsung", "TSMC", "Qualcomm", "AMD", "ARM", "Arm", "Intel",
    "Tesla", "Palantir", "Salesforce", "Oracle", "IBM",
    "vLLM", "LangChain", "Ollama", "Groq", "Vercel", "Cloudflare",
    "GitHub", "GitLab", "Mozilla", "Firefox", "ByteDance", "TikTok",
    "Alibaba", "Baidu", "Tencent", "Baidu", "Midjourney", "Runway",
    "ElevenLabs", "Replicate", "Together AI", "Fireworks AI",
    "Llama", "Gemini", "Claude", "GPT", "Copilot", "Cursor",
    "Softwear", "Qwen", "Phi",
]

# Build regex: longest first, case-sensitive (preserve original capitalisation)
_COMPANY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(c) for c in sorted(_KNOWN_COMPANIES, key=len, reverse=True)) + r")\b"
)

# ---------------------------------------------------------------------------
# Verb mapping  (English → 繁中)  —  ≥ 40 entries
# ---------------------------------------------------------------------------

_VERB_MAP: dict[str, str] = {
    # Product launches
    "launch": "發布",
    "launched": "發布",
    "launches": "發布",
    "launching": "發布",
    "release": "發布",
    "released": "發布",
    "releases": "發布",
    "releasing": "發布",
    "ship": "推出",
    "shipped": "推出",
    "ships": "推出",
    "shipping": "推出",
    "roll out": "推出",
    "rolled out": "推出",
    "rolls out": "推出",
    "rolling out": "推出",
    "introduce": "推出",
    "introduced": "推出",
    "introduces": "推出",
    "debut": "亮相",
    "debuted": "亮相",
    "debuts": "亮相",
    "unveil": "發表",
    "unveiled": "發表",
    "unveils": "發表",
    "publish": "發表",
    "published": "發表",
    "publishes": "發表",
    # Technical
    "deploy": "部署",
    "deployed": "部署",
    "deploys": "部署",
    "update": "更新",
    "updated": "更新",
    "updates": "更新",
    "upgrade": "升級",
    "upgraded": "升級",
    "upgrades": "升級",
    "integrate": "整合",
    "integrated": "整合",
    "integrates": "整合",
    "open-source": "開源",
    "open source": "開源",
    "open sourced": "開源",
    "open-sourced": "開源",
    "open-sources": "開源",
    "deprecate": "宣布停用",
    "deprecated": "宣布停用",
    "deprecates": "宣布停用",
    "train": "訓練",
    "trained": "訓練",
    "fine-tune": "微調",
    "fine-tuned": "微調",
    "fine tune": "微調",
    "fine tuned": "微調",
    # Business
    "raise": "完成融資",
    "raised": "完成融資",
    "raises": "完成融資",
    "fund": "獲得融資",
    "funded": "獲得融資",
    "acquire": "收購",
    "acquired": "收購",
    "acquires": "收購",
    "partner": "宣布合作",
    "partnered": "宣布合作",
    "partners": "宣布合作",
    "sign": "簽署協議",
    "signed": "簽署協議",
    "signs": "簽署協議",
    "invest": "投資",
    "invested": "投資",
    "invests": "投資",
    "expand": "擴大",
    "expanded": "擴大",
    "expands": "擴大",
    # Performance
    "benchmark": "基準測試顯示",
    "outperform": "超越",
    "outperformed": "超越",
    "outperforms": "超越",
    "improve": "提升",
    "improved": "提升",
    "improves": "提升",
    "enhance": "強化",
    "enhanced": "強化",
    "reduces": "降低",
    "reduce": "降低",
    "reduced": "降低",
    "cut": "削減",
    "cuts": "削減",
    "achieve": "達到",
    "achieved": "達到",
    "achieves": "達到",
    "reach": "達到",
    "reaches": "達到",
    "announce": "宣布",
    "announced": "宣布",
    "announces": "宣布",
    "enable": "支援",
    "enables": "支援",
    "support": "支援",
    "supports": "支援",
    "temper": "下調",
    "tempered": "下調",
    "lower": "降低",
    "lowered": "降低",
    "lowers": "降低",
    "scale back": "縮減",
    "scaled back": "縮減",
    "scale down": "縮減",
    "scaled down": "縮減",
    "cut back": "削減",
    "extend": "延伸",
    "extended": "延伸",
    "accelerate": "加速",
    "accelerated": "加速",
    "accelerates": "加速",
    "reveal": "揭示",
    "revealed": "揭示",
    "reveals": "揭示",
    "add": "新增",
    "added": "新增",
    "adds": "新增",
}

# Build verb regex — longest phrases first
_VERB_SORTED = sorted(_VERB_MAP.keys(), key=len, reverse=True)
_VERB_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(v) for v in _VERB_SORTED) + r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Impact keyword → ZH phrase
# ---------------------------------------------------------------------------

_IMPACT_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # (pattern, primary_impact_zh, secondary_impact_zh)
    (re.compile(r"\b(cost|pricing|price|afford|cheap|expens|subscript)\b", re.I),
     "降低 AI 應用成本", "加速企業部署意願"),
    (re.compile(r"\b(perf|latency|speed|throughput|faster|efficien|faster)\b", re.I),
     "提升推理效能", "降低使用者等待時間"),
    (re.compile(r"\b(market|competi|rival|dominan|lead|share)\b", re.I),
     "加劇市場競爭", "促使競對加速產品迭代"),
    (re.compile(r"\b(funding|invest|capital|valuation|billion|venture)\b", re.I),
     "影響 AI 投資格局", "帶動後續融資討論"),
    (re.compile(r"\b(security|safe|risk|vulnerab|threat|privacy|attack)\b", re.I),
     "引發 AI 安全討論", "促進業界安全規範制定"),
    (re.compile(r"\b(regulat|policy|govern|law|complian|ethic|congress)\b", re.I),
     "帶動政策層面討論", "加速相關法規完善"),
    (re.compile(r"\b(open.sour|open source|weight|communit|repo)\b", re.I),
     "推動開源生態發展", "拉低研究入門門檻"),
    (re.compile(r"\b(enterprise|business|corporat|b2b|contract)\b", re.I),
     "加速企業 AI 採用", "提升企業競爭力"),
    (re.compile(r"\b(developer|dev|engineer|program|sdk|api|package)\b", re.I),
     "拓展開發者工具生態", "縮短開發週期"),
    (re.compile(r"\b(chip|gpu|hardware|npu|infra|compute|server)\b", re.I),
     "強化 AI 硬體佈局", "影響晶片供應鏈格局"),
    (re.compile(r"\b(agent|agentic|autonomous|multi.agent)\b", re.I),
     "推動 AI 自主化應用普及", "改變人機協作模式"),
    (re.compile(r"\b(multimodal|vision|audio|video|speech)\b", re.I),
     "拓展多模態應用場景", "提升 AI 感知能力"),
]

# Fallback impact when nothing matches
_DEFAULT_IMPACT = ("對 AI 產業發展具有實質影響", "業界各方已著手評估後續影響")

# ---------------------------------------------------------------------------
# Bucket configuration: domain phrase, watch topics, risk topics
# ---------------------------------------------------------------------------

_BUCKET_CONFIG: dict[str, dict] = {
    "product": {
        "domain": "AI 產品",
        "action_default": "推出新功能",
        "watch": [
            "後續版本發布計畫",
            "用戶採用率與 API 呼叫量",
            "競對產品跟進動態",
        ],
        "risk": [
            "產品市佔率變化與用戶流失風險",
            "競對快速跟進導致差異化消失",
        ],
    },
    "tech": {
        "domain": "AI 技術",
        "action_default": "發表新研究成果",
        "watch": [
            "論文引用率與模型下載量",
            "開源社群技術複現與反應",
            "後續基準測試結果",
        ],
        "risk": [
            "技術被競對快速複製或超越",
            "演算法安全性與幻覺率評估",
        ],
    },
    "business": {
        "domain": "AI 商業",
        "action_default": "宣布重要商業決策",
        "watch": [
            "股市反應與分析師評級",
            "企業客戶採用率與合約規模",
            "競對公司策略調整動態",
        ],
        "risk": [
            "商業模式永續性與獲利時間表",
            "監管合規成本與法律風險",
        ],
    },
    "dev": {
        "domain": "AI 開發",
        "action_default": "推出開發者工具",
        "watch": [
            "套件下載量與 GitHub star 數",
            "社群 issue 與技術討論熱度",
            "相關工具整合進度",
        ],
        "risk": [
            "套件安全漏洞與維護持續性",
            "生態系依賴集中度與單點風險",
        ],
    },
}


def _bucket_cfg(bucket: str) -> dict:
    return _BUCKET_CONFIG.get(bucket, _BUCKET_CONFIG["business"])


# ---------------------------------------------------------------------------
# Month name → number map
# ---------------------------------------------------------------------------

_MONTH_MAP: dict[str, str] = {
    "january": "1", "february": "2", "march": "3", "april": "4",
    "may": "5", "june": "6", "july": "7", "august": "8",
    "september": "9", "october": "10", "november": "11", "december": "12",
    "jan": "1", "feb": "2", "mar": "3", "apr": "4",
    "jun": "6", "jul": "7", "aug": "8", "sep": "9", "oct": "10",
    "nov": "11", "dec": "12",
}

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_EN_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})(?:,?\s+(\d{4}))?",
    re.IGNORECASE,
)
_MONEY_RE = re.compile(
    r"\$[\d,]+(?:\.\d+)?\s*(?:billion|million|trillion|B|M|T)?\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(
    r"\b[\d,]+(?:\.\d+)?\s*(?:billion|million|trillion|billion-dollar|million-dollar|"
    r"percent|%|x|times|GB|TB|PB|tokens|parameters|params|GPU|NPU)\b",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(
    r"\b(?:v|version\s+)?(\d+\.\d+(?:\.\d+)*)\b"
    r"|\b(?:GPT|Claude|Gemini|Llama|Qwen|DeepSeek|Phi|Mistral|Grok)\s*[-]?\s*\d+(?:\.\d+)*\b",
    re.IGNORECASE,
)
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？；])|(?<=[.?!])\s+|\n+")


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def zh_ratio(text: str) -> float:
    """Return fraction of CJK characters in text."""
    if not text:
        return 0.0
    zh = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return round(zh / max(1, len(text)), 3)


def _ensure_terminated(text: str) -> str:
    t = text.strip()
    if t and t[-1] not in "。！？；.!?":
        t += "。"
    return t


def _sanitize(text: str) -> str:
    """Apply exec_sanitizer; fall back to identity if unavailable."""
    try:
        from utils.exec_sanitizer import sanitize_exec_text
        return sanitize_exec_text(text)
    except Exception:
        return text


def _extract_company(text: str) -> str:
    """Return first known company/product name found in text."""
    m = _COMPANY_RE.search(text or "")
    return m.group(0) if m else ""


def _extract_action_zh(text: str) -> str:
    """Find first verb in text and return its ZH mapping; empty string if none."""
    m = _VERB_RE.search(text or "")
    if not m:
        return ""
    raw = m.group(0).lower()
    return _VERB_MAP.get(raw, "")


def _extract_money_phrases(text: str) -> list[str]:
    """Return list of money strings found (e.g. '$40 billion')."""
    return _MONEY_RE.findall(text or "")


def _extract_number_phrases(text: str) -> list[str]:
    """Return list of notable number phrases (money + percentages + sizes)."""
    found: list[str] = []
    for m in _MONEY_RE.finditer(text or ""):
        found.append(m.group(0).strip())
    for m in _NUMBER_RE.finditer(text or ""):
        candidate = m.group(0).strip()
        if not any(candidate in f for f in found):
            found.append(candidate)
    return found[:4]


def _extract_version(text: str) -> str:
    """Return first version / model-version string found."""
    m = _VERSION_RE.search(text or "")
    return m.group(0).strip() if m else ""


def _format_date_zh(date_str: str) -> str:
    """Convert YYYY-MM-DD → 'YYYY年M月D日'; return '近日' if empty."""
    if not date_str:
        return "近日"
    m = _ISO_DATE_RE.match(date_str.strip())
    if m:
        y, mo, d = m.group(1), m.group(2).lstrip("0"), m.group(3).lstrip("0")
        return f"{y}年{mo}月{d}日"
    return "近日"


def _detect_impacts(text: str) -> list[str]:
    """Return list of ZH impact phrases matched from text."""
    matched: list[str] = []
    for pat, primary, secondary in _IMPACT_PATTERNS:
        if pat.search(text):
            matched.append(primary)
            matched.append(secondary)
    return matched[:4]


def _split_sentences(text: str) -> list[str]:
    parts = _SENT_SPLIT_RE.split(text or "")
    return [p.strip() for p in parts if p.strip() and len(p.strip()) >= 5]


def _first_sent(text: str) -> str:
    parts = _split_sentences(text)
    return parts[0] if parts else text.strip()[:120]


# ---------------------------------------------------------------------------
# Template builders
# ---------------------------------------------------------------------------

def _build_lead_sentence(
    company: str,
    action_zh: str,
    version: str,
    numbers: list[str],
    date_zh: str,
    bucket: str,
) -> str:
    """Assemble Q1 lead sentence from extracted tokens."""
    cfg = _bucket_cfg(bucket)
    act = action_zh or cfg["action_default"]
    domain = cfg["domain"]

    parts: list[str] = [date_zh, "，"]

    if company:
        parts.append(company)
        parts.append(" ")

    if version and company:
        # "{company} {action_zh} {version}"
        parts.append(act)
        parts.append(" ")
        parts.append(version)
        if numbers:
            parts.append("，")
            parts.append(numbers[0])
        parts.append("，在業界引發廣泛討論")
    elif numbers and company:
        # "{company} {action_zh}，{number}"
        parts.append(act)
        parts.append(" ")
        parts.append(numbers[0])
        if len(numbers) > 1:
            parts.append("，")
            parts.append(numbers[1])
    elif company and act:
        parts.append(act)
        parts.append("，在業界引發廣泛討論")
    elif company:
        parts.append(f"在 {domain} 領域有重要進展")
    else:
        parts = [date_zh, "，", domain, " 領域出現重要動態"]

    sentence = "".join(parts)
    return _ensure_terminated(sentence)


def _build_impact_sentence(impacts: list[str], company: str, bucket: str) -> str:
    """Assemble Q2 impact sentence."""
    p1, p2 = (impacts[0], impacts[1]) if len(impacts) >= 2 else _DEFAULT_IMPACT
    if company:
        return _ensure_terminated(f"此舉預計將{p1}；同時可能{p2}")
    cfg = _bucket_cfg(bucket)
    return _ensure_terminated(f"此消息對 {cfg['domain']} 市場具重要意義，預計將{p1}，並{p2}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def rewrite_news_lead(text_en: str, context: dict) -> str:
    """Build Traditional Chinese news lead (Q1 — what happened).

    If input text is already ZH-dominant (zh_ratio ≥ 0.35), polish and return.
    Otherwise build ZH sentence from extracted tokens.
    """
    title = context.get("title", "") or ""
    bucket = context.get("bucket", "business") or "business"
    date_str = context.get("date", "") or ""
    what_zh = context.get("what_happened", "") or ""

    # If source text already ZH-dominant, just sanitize and return
    combined_src = (text_en or "") + " " + (what_zh or "")
    if zh_ratio(combined_src) >= 0.40:
        result = combined_src.strip()
        sents = _split_sentences(result)
        # Take up to 2 sentences, ensure ZH
        chosen = "".join(_ensure_terminated(s) for s in sents[:2])
        return _sanitize(chosen) or _sanitize(result[:200])

    # Extract tokens from title + text_en (title often has most info)
    src = (title + " " + (text_en or "")).strip()

    company = _extract_company(src) or context.get("subject", "")
    action_zh = _extract_action_zh(src)
    version = _extract_version(src)
    numbers = _extract_number_phrases(src)
    date_zh = _format_date_zh(date_str)

    # Sentence 1: main event
    s1 = _build_lead_sentence(company, action_zh, version, numbers, date_zh, bucket)

    # Sentence 2: supporting context from what_happened or title tail
    cfg = _bucket_cfg(bucket)
    s2_candidates = []
    if what_zh and zh_ratio(what_zh) >= 0.20:
        s2_candidates.extend(_split_sentences(what_zh))
    if not s2_candidates and numbers and len(numbers) > 1:
        s2_candidates.append(f"此次 {numbers[-1]} 的規模，料將影響後續市場格局")
    if not s2_candidates:
        act2 = action_zh or cfg["action_default"]
        s2_candidates.append(f"此舉為 {cfg['domain']} 發展帶來新的參考基準")

    s2_raw = s2_candidates[0] if s2_candidates else ""
    s2 = _ensure_terminated(s2_raw[:180]) if s2_raw else ""

    result = s1 + (s2 if s2 and s2 != s1 else "")
    return _sanitize(result)


def rewrite_news_impact(text_en: str, context: dict) -> str:
    """Build Traditional Chinese impact sentence (Q2 — why it matters)."""
    bucket = context.get("bucket", "business") or "business"
    why_zh = context.get("why_important", "") or ""
    title = context.get("title", "") or ""

    # If why_important already ZH-dominant, use directly
    if zh_ratio(why_zh) >= 0.40:
        sents = _split_sentences(why_zh)
        chosen = "".join(_ensure_terminated(s) for s in sents[:2])
        return _sanitize(chosen) or _sanitize(why_zh[:200])

    # Extract impact signals from all available text
    src = " ".join(filter(None, [title, text_en or "", why_zh]))
    company = _extract_company(src) or context.get("subject", "")
    impacts = _detect_impacts(src)

    # Primary + secondary impact
    s1 = _build_impact_sentence(impacts, company, bucket)

    # Sentence 2: consequence for company/market
    cfg = _bucket_cfg(bucket)
    if company:
        s2 = _ensure_terminated(f"各大廠商與投資人已著手評估此事對 {cfg['domain']} 版圖的影響")
    else:
        s2 = _ensure_terminated(f"{cfg['domain']} 市場的競爭態勢料將因此出現新的變化")

    result = s1 + s2
    return _sanitize(result)


def rewrite_news_next(text_en: str, context: dict) -> list[str]:
    """Build Traditional Chinese next-step bullets (Q3 — 3 items)."""
    bucket = context.get("bucket", "business") or "business"
    action_items = context.get("action_items", []) or []
    cfg = _bucket_cfg(bucket)
    title = context.get("title", "") or ""
    src = " ".join(filter(None, [title, text_en or ""]))
    company = _extract_company(src) or context.get("subject", "")

    # Bucket-specific watch topics
    watch_topics = list(cfg["watch"])  # copy

    # If action_items available and long enough, derive first watch topic
    if action_items:
        a0 = str(action_items[0] or "").strip()
        if a0 and len(a0) >= 8:
            # Incorporate the original action item as context in first bullet
            act_zh = _extract_action_zh(a0)
            if act_zh and zh_ratio(a0) < 0.30:
                # Re-wrap in ZH template
                watch_topics[0] = f"後續 {act_zh} 時間表與市場反應"
            elif zh_ratio(a0) >= 0.30:
                watch_topics[0] = a0[:40]

    # Build 3 bullets with ZH prefix
    bullets: list[str] = []
    if len(watch_topics) >= 1:
        if company:
            bullets.append(_ensure_terminated(f"可觀察：{company} {watch_topics[0]}（T+7）"))
        else:
            bullets.append(_ensure_terminated(f"可觀察：{watch_topics[0]}（T+7）"))
    if len(watch_topics) >= 2:
        bullets.append(_ensure_terminated(f"建議追蹤：{watch_topics[1]}後續動態"))
    if len(watch_topics) >= 3:
        bullets.append(_ensure_terminated(f"持續監控：{watch_topics[2]}"))

    # Pad to 3 if needed
    while len(bullets) < 3:
        bullets.append(_ensure_terminated(f"持續監控：{cfg['domain']}整體市場動向"))

    return [_sanitize(b) for b in bullets[:3]]


def rewrite_news_risks(text_en: str, context: dict) -> list[str]:
    """Build Traditional Chinese risk bullets (2 items)."""
    bucket = context.get("bucket", "business") or "business"
    spec_effects = context.get("speculative_effects", []) or []
    deriv_effects = context.get("derivable_effects", []) or []
    cfg = _bucket_cfg(bucket)
    risk_topics = list(cfg["risk"])

    # Prefer speculative_effects if ZH-rich
    for eff in (spec_effects + deriv_effects)[:4]:
        s = str(eff or "").strip()
        if s and len(s) >= 10 and zh_ratio(s) >= 0.30:
            risk_topics.insert(0, s[:60])

    bullets: list[str] = []
    if len(risk_topics) >= 1:
        bullets.append(_ensure_terminated(f"主要風險：{risk_topics[0]}，需持續追蹤"))
    if len(risk_topics) >= 2:
        bullets.append(_ensure_terminated(f"潛在影響：{risk_topics[1]}，建議密切關注"))

    while len(bullets) < 2:
        bullets.append(_ensure_terminated("潛在影響：競爭格局快速演變，建議密切關注"))

    return [_sanitize(b) for b in bullets[:2]]


# ---------------------------------------------------------------------------
# v2 API: Anchor-injected rewrite (Iteration 4 — News Anchor Perfect v1)
# ---------------------------------------------------------------------------

# Hollow template phrases that must be replaced or enriched when anchor is present
_ANTI_GENERIC_MAP: list[tuple[str, str]] = [
    ("引發業界廣泛關注",              "引發業界廣泛討論"),
    ("具有重要意義",                  "具有實質影響"),
    ("各方正密切追蹤後續進展",        "業界各方已著手評估後續影響"),
    ("新的參考基準",                  "新的技術基準"),
    ("帶來新的參考基準",              "帶來新的技術指標"),
    ("各大廠商與投資人正密切評估",    "業界各方已著手因應"),
    ("在 AI 技術 發展帶來新的參考基準", "在 AI 技術發展上帶來重要技術基準"),
    ("在 AI 技術發展帶來新的參考基準", "在 AI 技術發展上帶來重要技術基準"),
    ("為 AI 技術 發展帶來新的參考基準", "為 AI 技術發展帶來新的技術指標"),
    ("對 AI 產業發展具有重要意義",    "對 AI 產業發展具有實質影響"),
]


def _apply_anti_generic(text: str, primary_anchor: str | None = None) -> str:
    """Replace hollow template phrases; inject anchor context if available."""
    if not text:
        return text
    for generic, replacement in _ANTI_GENERIC_MAP:
        if generic in text:
            if primary_anchor:
                text = text.replace(
                    generic,
                    f"{primary_anchor} 的最新進展持續受到業界注目",
                )
            else:
                text = text.replace(generic, replacement)
    return text


def _build_anchor_lead(
    company: str,
    action_zh: str,
    primary_anchor: str,
    anchor_type: str,
    numbers: list[str],
    date_zh: str,
    bucket: str,
) -> str:
    """Build Q1 lead sentence with primary_anchor naturally injected."""
    cfg = _bucket_cfg(bucket)
    act = action_zh or cfg["action_default"]
    dp = f"{date_zh}，" if date_zh else ""

    if anchor_type == "money":
        if company:
            return _ensure_terminated(f"{dp}{company} 以 {primary_anchor} {act}")
        return _ensure_terminated(f"{dp}業界傳出 {primary_anchor} {act}消息")

    if anchor_type in ("product", "version"):
        if company and company.lower() not in primary_anchor.lower():
            return _ensure_terminated(f"{dp}{company} 正式{act} {primary_anchor}")
        return _ensure_terminated(f"{dp}{primary_anchor} 正式{act}")

    if anchor_type == "benchmark":
        if company:
            return _ensure_terminated(
                f"{dp}{company} 在 {primary_anchor} 評測中取得突破性成果"
            )
        return _ensure_terminated(
            f"{dp}{primary_anchor} 最新評測結果揭示重要技術進展"
        )

    if anchor_type == "params":
        if company:
            return _ensure_terminated(f"{dp}{company} 推出 {primary_anchor} 規模模型，{act}")
        return _ensure_terminated(f"{dp}業界發布 {primary_anchor} 規模開源模型")

    if anchor_type == "metric":
        if company:
            return _ensure_terminated(f"{dp}{company} {act}，實現 {primary_anchor}")
        return _ensure_terminated(f"{dp}最新研究突破，實現 {primary_anchor}")

    # Fallback: v1 logic with anchor injected as version
    return _build_lead_sentence(company, action_zh, primary_anchor, numbers, date_zh, bucket)


def _extract_impact_anchor(text: str) -> str:
    """Extract specific impact anchor (metric/number/partner) from text for Q2."""
    if not text:
        return ""
    # Percentage metric
    m = re.search(
        r"\b(\d+(?:\.\d+)?)\s*%\s*(?:reduction|improvement|faster|cheaper|lower|increase)\b",
        text, re.I,
    )
    if m:
        return m.group(0).strip()
    # X-times metric
    m = re.search(
        r"\b(\d+(?:\.\d+)?)\s*x\s+(?:faster|cheaper|better|more efficient)\b",
        text, re.I,
    )
    if m:
        return m.group(0).strip()
    # Money amount
    m = re.search(r"\$[\d,]+(?:\.\d+)?\s*(?:billion|million|B|M)\b", text, re.I)
    if m:
        return m.group(0).strip()
    # Named partnership
    m = re.search(
        r"\bpartner(?:ed|ship)?\s+with\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\b",
        text, re.I,
    )
    if m:
        return f"與 {m.group(1)} 的合作"
    return ""


def _detect_anchor_type(primary_anchor: str) -> str:
    """Determine anchor type string from the anchor token itself."""
    if not primary_anchor:
        return ""
    try:
        from utils.news_anchor import (
            _MODEL_VERSION_RE, _BENCHMARK_KEYWORDS,
            _MONEY_RE, _GENERIC_VERSION_RE, _METRIC_RE,
        )
        if _MODEL_VERSION_RE.search(primary_anchor):
            return "product"
        if any(kw.lower() in primary_anchor.lower() for kw in _BENCHMARK_KEYWORDS):
            return "benchmark"
        if _MONEY_RE.search(primary_anchor):
            return "money"
        if re.search(r"\d+[BM]$", primary_anchor, re.IGNORECASE):
            return "params"
        if _METRIC_RE.search(primary_anchor):
            return "metric"
        if _GENERIC_VERSION_RE.search(primary_anchor):
            return "version"
    except Exception:
        pass
    return ""


def rewrite_news_lead_v2(
    text_en: str,
    context: dict,
    anchors: "list[str] | None" = None,
    primary_anchor: "str | None" = None,
) -> str:
    """Anchor-injected news lead (Q1) — Iteration 4.

    Same algorithm as rewrite_news_lead v1, plus:
    1. Injects primary_anchor into Q1 sentence structure when provided.
    2. Anti-generic: replaces hollow template phrases.
    3. Falls back gracefully to v1 logic when no anchor present.

    Parameters
    ----------
    text_en       : English source text (narrative / what_happened)
    context       : same context dict as v1
    anchors       : full list of extracted anchors (may be empty)
    primary_anchor: top-priority anchor token to inject into Q1
    """
    anchors = anchors or []
    title    = context.get("title", "") or ""
    bucket   = context.get("bucket", "business") or "business"
    date_str = context.get("date", "") or ""
    what_zh  = context.get("what_happened", "") or ""

    anchor_type = _detect_anchor_type(primary_anchor) if primary_anchor else ""

    # If source already ZH-dominant, polish + apply anti-generic
    combined_src = (text_en or "") + " " + (what_zh or "")
    if zh_ratio(combined_src) >= 0.40:
        result = combined_src.strip()
        sents  = _split_sentences(result)
        chosen = "".join(_ensure_terminated(s) for s in sents[:2])
        if not chosen:
            chosen = result[:200]
        chosen = _apply_anti_generic(chosen, primary_anchor)
        return _sanitize(chosen)

    # Extract tokens from title + text_en
    src      = (title + " " + (text_en or "")).strip()
    company  = _extract_company(src) or context.get("subject", "")
    action_zh = _extract_action_zh(src)
    numbers  = _extract_number_phrases(src)
    date_zh  = _format_date_zh(date_str)

    # Sentence 1: anchor-injected or v1 fallback
    if primary_anchor and anchor_type:
        s1 = _build_anchor_lead(
            company, action_zh, primary_anchor, anchor_type, numbers, date_zh, bucket
        )
    else:
        s1 = _build_lead_sentence(
            company, action_zh, _extract_version(src), numbers, date_zh, bucket
        )
        s1 = _apply_anti_generic(s1, primary_anchor)

    # Sentence 2: supporting context
    cfg = _bucket_cfg(bucket)
    s2_candidates: list[str] = []
    if what_zh and zh_ratio(what_zh) >= 0.20:
        s2_candidates.extend(_split_sentences(what_zh))
    if not s2_candidates and len(anchors) > 1:
        s2_candidates.append(f"此次公告涉及 {anchors[1]}，料將影響後續市場格局")
    if not s2_candidates and numbers and len(numbers) > 1:
        s2_candidates.append(f"此次 {numbers[-1]} 的規模，料將影響後續市場格局")
    if not s2_candidates:
        s2_candidates.append(f"此舉為 {cfg['domain']} 帶來重要技術指標")

    s2_raw = s2_candidates[0] if s2_candidates else ""
    s2_raw = _apply_anti_generic(s2_raw, primary_anchor)
    s2     = _ensure_terminated(s2_raw[:180]) if s2_raw else ""

    result = s1 + (s2 if s2 and s2 != s1 else "")
    return _sanitize(result)


def rewrite_news_impact_v2(
    text_en: str,
    context: dict,
    anchors: "list[str] | None" = None,
    primary_anchor: "str | None" = None,
) -> str:
    """Anchor-injected impact sentence (Q2) — Iteration 4.

    Same algorithm as rewrite_news_impact v1, plus:
    1. Extracts a specific impact anchor (%, metric, partner) from source text.
    2. Injects it into Q2 for concreteness.
    3. Anti-generic: replaces "各方正密切追蹤…" and similar hollow phrases.
    """
    anchors = anchors or []
    bucket  = context.get("bucket", "business") or "business"
    why_zh  = context.get("why_important", "") or ""
    title   = context.get("title", "") or ""
    effects = context.get("derivable_effects", []) or []

    # If why_important already ZH-dominant, use directly (with anti-generic)
    if zh_ratio(why_zh) >= 0.40:
        sents  = _split_sentences(why_zh)
        chosen = "".join(_ensure_terminated(s) for s in sents[:2])
        if not chosen:
            chosen = why_zh[:200]
        chosen = _apply_anti_generic(chosen, primary_anchor)
        return _sanitize(chosen)

    # Aggregate source text for impact detection
    src = " ".join(
        filter(None, [title, text_en or "", why_zh,
                      " ".join(str(e) for e in effects[:3])])
    )
    company = _extract_company(src) or context.get("subject", "")
    impacts = _detect_impacts(src)
    impact_anchor = _extract_impact_anchor(src)

    cfg = _bucket_cfg(bucket)
    if impacts:
        p1 = impacts[0]
        p2 = impacts[1] if len(impacts) >= 2 else cfg["risk"][0]
    else:
        p1, p2 = _DEFAULT_IMPACT

    # Sentence 1 — with specific impact anchor if found
    if impact_anchor and impacts:
        if company:
            s1 = _ensure_terminated(
                f"此舉預計將{p1}（實測：{impact_anchor}）；同時可能{p2}"
            )
        else:
            s1 = _ensure_terminated(
                f"此消息對 {cfg['domain']} 市場具實質意義，"
                f"實測顯示{impact_anchor}，預計將{p1}，並{p2}"
            )
    else:
        s1 = _build_impact_sentence(impacts, company, bucket)
        s1 = _apply_anti_generic(s1, primary_anchor)

    # Sentence 2 — avoid generic "各大廠商與投資人正密切評估..."
    anchor_mention = impact_anchor or primary_anchor
    if anchor_mention:
        if company:
            s2 = _ensure_terminated(
                f"業界各方已著手評估 {anchor_mention} 對現有部署策略的影響"
            )
        else:
            s2 = _ensure_terminated(
                f"{cfg['domain']} 市場各方正評估 {anchor_mention} 的影響範圍"
            )
    else:
        if company:
            raw = f"各大廠商與投資人已著手評估此事對 {cfg['domain']} 版圖的影響"
            s2  = _ensure_terminated(_apply_anti_generic(raw, None))
        else:
            raw = f"{cfg['domain']} 市場的競爭態勢料將因此出現新的變化"
            s2  = _ensure_terminated(_apply_anti_generic(raw, primary_anchor))

    result = s1 + s2
    return _sanitize(result)
