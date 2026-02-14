"""Shared content-intelligence layer for executive output generators.

Centralizes:
- Curated/index page detection
- 6-column decision card construction (no homework, no template sentences)
- Banned-word sanitization
- Responsibility mapping
- Content quality guards (no empty talk)

Used by: ppt_generator.py, doc_generator.py
"""

from __future__ import annotations

import re

from schemas.education_models import EduNewsCard

# ---------------------------------------------------------------------------
# Banned words — sanitize all output text
# ---------------------------------------------------------------------------
BANNED_WORDS = [
    "ai捕捉", "AI Intel", "Z1", "Z2", "Z3", "Z4", "Z5",
    "pipeline", "ETL", "verify_run", "ingestion", "ai_core",
]

# Patterns that indicate empty-talk / homework / template sentences
_HOMEWORK_PATTERNS = [
    r"本週內[：:]",
    r"→\s*產出[：:]",
    r"摘要筆記",
    r"搜尋[「『]",
    r"去查|去找|去搜",
    r"建議追蹤",
    r"值得持續觀察",
    r"反映.*重要.*趨勢",
    r"此事件反映",
    r"值得.*關注.*後續",
    r"兩週內[：:].*評估",
    r"產出[：:].*評估表",
    r"影響評估表",
]
_HOMEWORK_RE = re.compile("|".join(_HOMEWORK_PATTERNS), re.IGNORECASE)

# Keywords suggesting a curated/index/archive page rather than a single event
_INDEX_PAGE_KEYWORDS = [
    "curated list", "curated", "rss feed", "rss", "entries for the year",
    "collecting", "archive", "overview", "stats", "year in review",
    "changelog", "release notes list", "index of", "table of contents",
]

# Responsibility mapping for "要問誰" column
RESPONSIBILITY_MAP = {
    "綜合": "策略長/PM",
    "tech": "策略長/PM",
    "科技/技術": "研發/CTO",
    "人工智慧": "研發/CTO",
    "資安": "資安長",
    "政策/監管": "法務",
    "法規": "法務",
    "金融/財經": "財務長/CFO",
    "創業/投融資": "策略長/PM",
    "氣候/能源": "營運/COO",
    "併購/企業": "策略長/CEO",
    "消費電子": "產品/PM",
    "遊戲/娛樂": "產品/PM",
    "雲": "研發/CTO",
    "AI": "研發/CTO",
    "工程": "研發/CTO",
    "產品": "產品/PM",
    "市場": "產品/市場",
}


def sanitize(text: str) -> str:
    """Remove banned words, then strip homework/template sentences."""
    result = text
    for bw in BANNED_WORDS:
        result = result.replace(bw, "")
    # Remove homework-style fragments
    result = _HOMEWORK_RE.sub("", result).strip()
    # Clean up leftover punctuation/whitespace
    result = re.sub(r"[「」『』]\s*[「」『』]", "", result)
    result = re.sub(r"\s{2,}", " ", result).strip()
    return result


def responsible_party(category: str) -> str:
    """Map category to responsible party."""
    cat = (category or "").strip()
    if cat in RESPONSIBILITY_MAP:
        return RESPONSIBILITY_MAP[cat]
    # Fuzzy match on substring
    cat_lower = cat.lower()
    for key, val in RESPONSIBILITY_MAP.items():
        if key.lower() in cat_lower or cat_lower in key.lower():
            return val
    return "策略長/PM"


def is_index_page(card: EduNewsCard) -> bool:
    """Detect if a card represents a curated/index/archive page, not a single event."""
    combined = f"{card.title_plain} {card.what_happened}".lower()
    hits = sum(1 for kw in _INDEX_PAGE_KEYWORDS if kw in combined)
    return hits >= 1


def _clean_text(text: str, max_len: int) -> str:
    """Sanitize + truncate."""
    return sanitize(text)[:max_len] if text else ""


# ---------------------------------------------------------------------------
# 6-column decision card builder
# ---------------------------------------------------------------------------


def build_decision_card(card: EduNewsCard) -> dict[str, list[str] | str]:
    """Build a structured 6-column decision card from an EduNewsCard.

    Returns dict with keys: event, facts, effects, risks, actions, owner.
    Each value is either a str or list[str].
    All text is sanitized and free of homework/template sentences.
    """
    is_index = is_index_page(card)

    # 1) 事件一句話 (≤22 chars)
    if is_index:
        event = "來源疑似彙整索引頁，非單一事件"
    else:
        raw_event = sanitize(card.what_happened[:22]) if card.what_happened else ""
        event = raw_event if raw_event and len(raw_event) > 4 else "事件摘要資料不足"

    # 2) 已知事實 (3 points)
    facts: list[str] = []
    if is_index:
        # Extract verifiable page-level facts
        if "rss" in (card.what_happened or "").lower():
            facts.append("此來源提供 RSS 訂閱格式")
        if re.search(r"20\d{2}", card.what_happened or ""):
            year_match = re.search(r"(20\d{2})", card.what_happened or "")
            facts.append(f"頁面涵蓋年份：{year_match.group(1)}")
        if card.title_plain:
            facts.append(f"來源標題：{sanitize(card.title_plain[:50])}")
        if len(facts) < 3:
            facts.append("缺口：此頁為彙整/索引，無法提取單一事件的事實")
    else:
        # Normal card: prefer confirmed facts, then evidence
        for f in (card.fact_check_confirmed or [])[:3]:
            cleaned = sanitize(f[:60])
            if cleaned and len(cleaned) > 5:
                facts.append(cleaned)
        if not facts:
            for e in (card.evidence_lines or [])[:3]:
                cleaned = sanitize(e[:60])
                if cleaned and len(cleaned) > 5:
                    facts.append(cleaned)
        if not facts:
            facts.append("缺口：缺可驗證來源、時間或主體")

    # Pad to 3 if needed
    gap_templates = [
        "缺口：缺可驗證來源或原始出處",
        "缺口：缺事件時間或主體",
        "缺口：缺第三方佐證",
    ]
    idx = 0
    while len(facts) < 3 and idx < len(gap_templates):
        if gap_templates[idx] not in facts:
            facts.append(gap_templates[idx])
        idx += 1

    # 3) 可能影響 (2-3 points)
    effects: list[str] = []
    if is_index:
        effects = [
            "若此來源多為索引頁，會稀釋每日情報的決策價值",
            "資訊來源品質下降可能導致漏抓真正的重要事件",
        ]
    else:
        for eff in (card.derivable_effects or [])[:3]:
            cleaned = sanitize(eff[:50])
            if cleaned and len(cleaned) > 5 and not _HOMEWORK_RE.search(cleaned):
                effects.append(cleaned)
        if not effects:
            # Re-derive from what_happened/why_important, but only if substantive
            for src in [card.why_important, card.what_happened]:
                cleaned = sanitize((src or "")[:50])
                if cleaned and len(cleaned) > 10 and not _HOMEWORK_RE.search(cleaned):
                    effects.append(f"潛在影響：{cleaned}")
                    break
        if not effects:
            effects.append("缺口：影響面尚待分析（原始資料不足）")

    # 4) 主要風險 (2 points)
    risks: list[str] = []
    if is_index:
        risks = [
            "若持續納入索引頁，可能遮蔽真正需要決策的事件",
            "資料品質下降→決策基礎受損",
        ]
    else:
        for r in (card.speculative_effects or [])[:2]:
            cleaned = sanitize(r[:50])
            if cleaned and len(cleaned) > 5 and not _HOMEWORK_RE.search(cleaned):
                risks.append(cleaned)
        if not risks:
            risks.append("缺口：風險評估需更多背景資料")

    # 5) 建議決策/動作 (1-2 points) — NEVER homework
    actions: list[str] = []
    if is_index:
        actions = [
            "決策者需確認：保留此來源 or 降權/移除？",
            "指派負責人評估此來源的資訊品質",
        ]
    else:
        for a in (card.action_items or [])[:2]:
            cleaned = sanitize(a[:55])
            if cleaned and len(cleaned) > 5 and not _HOMEWORK_RE.search(cleaned):
                actions.append(cleaned)
        if not actions:
            actions.append("決策者需確認：此事件是否影響現有業務或專案排程？")

    # 6) 要問誰
    owner = responsible_party(card.category)

    return {
        "event": event[:22],
        "facts": facts[:3],
        "effects": effects[:3],
        "risks": risks[:2],
        "actions": actions[:2],
        "owner": owner,
    }


# ---------------------------------------------------------------------------
# Key term extraction (expanded stopwords, context-aware)
# ---------------------------------------------------------------------------

_STOP_WORDS = {
    # Common English
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
    "her", "was", "one", "our", "out", "has", "his", "how", "its", "may",
    "new", "now", "old", "see", "way", "who", "did", "get", "let", "say",
    "she", "too", "use", "with", "this", "that", "from", "have", "been",
    "will", "more", "when", "some", "than", "them", "what", "your", "each",
    "make", "like", "into", "over", "such", "take", "year", "also", "back",
    "could", "would", "about", "after", "other", "which", "their", "there",
    "first", "these", "those", "being", "where", "every", "should", "because",
    "http", "https", "www", "com", "org", "html", "json", "xml", "url",
    "via", "per", "etc", "just", "very", "much", "most", "only", "then",
    "here", "well", "still", "even", "does", "done", "going", "want",
    # Additional common non-technical words
    "said", "says", "many", "been", "were", "they", "them", "both",
    "same", "while", "during", "before", "since", "between", "under",
    "within", "through", "already", "several", "another", "however",
    "including", "according", "although", "using", "based", "part",
    "report", "reports", "reported", "company", "companies", "people",
    "data", "time", "made", "last", "next", "down", "help", "show",
    "shows", "showed", "look", "need", "needs", "work", "works",
    "plan", "plans", "move", "call", "called", "keep", "start",
    "started", "come", "came", "think", "given", "give", "gave",
    "found", "find", "known", "know", "long", "high", "lead",
    "early", "late", "left", "right", "real", "open", "test",
    "tests", "tested", "added", "used", "set", "run", "big",
    # File/web artifacts
    "file", "files", "page", "pages", "link", "links", "site",
    "image", "images", "text", "click", "view", "read", "list",
    "item", "items", "type", "name", "code", "line", "lines",
    "source", "content", "title", "post", "blog", "web",
    # Generic verbs/adjectives in news
    "major", "latest", "recent", "global", "full", "total",
    "million", "billion", "percent", "number", "version",
}

_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")


def extract_key_terms(card: EduNewsCard) -> list[str]:
    """Extract unique English technical terms from card fields.

    Improved: broader stopword list, prefers multi-word or capitalized terms.
    """
    sources = [
        card.title_plain or "",
        card.what_happened or "",
        card.technical_interpretation or "",
    ]
    for line in (card.evidence_lines or []):
        sources.append(line)
    for line in (card.fact_check_confirmed or []):
        sources.append(line)
    for line in (card.derivable_effects or []):
        sources.append(line)

    combined = " ".join(sources)
    raw_terms = _TERM_RE.findall(combined)

    seen: set[str] = set()
    unique: list[str] = []
    for t in raw_terms:
        low = t.lower()
        if low in _STOP_WORDS or low in seen or len(t) < 3:
            continue
        seen.add(low)
        unique.append(t)

    return unique[:5]


# Keep old name as alias for backward compatibility in tests
_extract_english_terms = extract_key_terms


# ---------------------------------------------------------------------------
# Context-aware term explainer (replaces old Q/A template approach)
# ---------------------------------------------------------------------------

# Curated explanations — keyed by lowercase term
from schemas.education_models import TERM_METAPHORS as _CURATED_TERMS


def build_term_explainer(card: EduNewsCard) -> list[dict[str, str]]:
    """Build context-aware term explanations for CEO audience.

    Returns list of dicts: [{"term": ..., "explain": ...}, ...]
    Each explanation references the card's actual content, not generic templates.
    """
    terms = extract_key_terms(card)
    if not terms:
        return []

    context_what = sanitize((card.what_happened or "")[:80])
    context_why = sanitize((card.why_important or "")[:80])

    results: list[dict[str, str]] = []
    for term in terms[:4]:
        low = term.lower()
        # 1) Check curated dictionary first
        curated = None
        for key, val in _CURATED_TERMS.items():
            if low == key.lower() or low in key.lower():
                curated = val
                break

        if curated:
            explain = curated
        elif context_what:
            # 2) Build explanation from card context
            explain = (
                f"在本則新聞中，{term} 指的是與「{context_what}」相關的"
                f"技術或概念。"
            )
            if context_why:
                explain += f"重要性在於：{context_why}"
        else:
            explain = f"{term}：此術語出現於本則新聞，建議查閱原文瞭解上下文。"

        results.append({"term": term, "explain": sanitize(explain)})

    return results


def build_term_explainer_lines(card: EduNewsCard) -> list[str]:
    """Flat-line version of build_term_explainer for text-based output."""
    items = build_term_explainer(card)
    lines: list[str] = []
    for item in items:
        lines.append(f"{item['term']}：{item['explain']}")
        lines.append("")
    return lines


# Keep old function name as alias so existing imports don't break
def build_term_explainer_qa(card: EduNewsCard) -> list[str]:
    """Backward-compatible alias — returns flat lines."""
    return build_term_explainer_lines(card)


# ---------------------------------------------------------------------------
# CEO article blocks (replaces Q/A format with article-style content)
# ---------------------------------------------------------------------------


def build_ceo_article_blocks(card: EduNewsCard) -> dict[str, str | list[str]]:
    """Build article-style content blocks for CEO-readable output.

    Returns dict with keys:
        headline_cn: Chinese headline (≤30 chars)
        one_liner: One-sentence summary of the event
        why_it_matters: Why this matters to the company (2-3 sentences)
        what_to_do: Concrete next step (1 sentence, no homework)
        quote: Key evidence quote from the source
        sources: list of source URLs
        owner: responsible party
    """
    dc = build_decision_card(card)

    # headline — prefer title, truncated
    headline = sanitize(card.title_plain[:30]) if card.title_plain else "事件摘要"

    # one_liner — what happened in one sentence
    if card.what_happened:
        one_liner = sanitize(card.what_happened[:120])
    else:
        one_liner = dc["event"]

    # why_it_matters — combine effects + risks into prose
    parts: list[str] = []
    for eff in dc["effects"]:
        if not eff.startswith("缺口"):
            parts.append(eff)
    for risk in dc["risks"]:
        if not risk.startswith("缺口"):
            parts.append(f"風險：{risk}")
    if card.why_important:
        cleaned = sanitize(card.why_important[:150])
        if cleaned and len(cleaned) > 10:
            parts.insert(0, cleaned)
    why_it_matters = "。".join(parts[:3]) + "。" if parts else "影響面待進一步分析。"

    # what_to_do — concrete action, never homework
    action = dc["actions"][0] if dc["actions"] else "待確認下一步"
    what_to_do = f"{action}（負責人：{dc['owner']}）"

    # quote — best evidence line
    quote = ""
    for line in (card.evidence_lines or []):
        cleaned = sanitize(line[:150])
        if cleaned and len(cleaned) > 15:
            quote = cleaned
            break
    if not quote:
        for line in (card.fact_check_confirmed or []):
            cleaned = sanitize(line[:150])
            if cleaned and len(cleaned) > 10:
                quote = cleaned
                break

    # sources
    sources: list[str] = []
    if card.source_url and card.source_url.startswith("http"):
        sources.append(card.source_url)

    return {
        "headline_cn": headline,
        "one_liner": one_liner,
        "why_it_matters": why_it_matters,
        "what_to_do": what_to_do,
        "quote": quote,
        "sources": sources,
        "owner": dc["owner"],
    }


def build_executive_qa(card: EduNewsCard, dc: dict) -> list[str]:
    """Build 總經理決策 QA lines, referencing actual card data (not templates)."""
    short_title = sanitize(card.title_plain[:20])
    fact_ref = dc["facts"][0] if dc["facts"] else "資料不足"
    effect_ref = dc["effects"][0] if dc["effects"] else "待評估"
    risk_ref = dc["risks"][0] if dc["risks"] else "低"
    action_ref = dc["actions"][0] if dc["actions"] else "待確認"
    owner = dc["owner"]

    lines = [
        f"Q1：「{short_title}」影響收入/成本/合規/交付節奏？",
        f"→ 根據「{fact_ref}」，預計「{effect_ref}」。風險為「{risk_ref}」。",
        "",
        f"Q2：今天要拍板嗎？延後 2 週代價？",
        f"→ {action_ref}。建議由{owner}於本週內回覆評估結論。",
        "",
        f"Q3：最小試探動作（<=1週 <=1 owner）？",
        f"→ 指派{owner}用 1 個工作天完成初步影響評估並回報。",
    ]
    return lines
