"""Shared content-intelligence layer for executive output generators.

Centralizes:
- Curated/index page detection
- Non-event / index page filtering for CEO deck
- Grammar-safe sanitization (never creates broken sentences)
- 6-column decision card construction (no homework, no template sentences)
- CEO article blocks with real content
- Context-aware term explainer with curated dictionary
- Responsibility mapping

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

# System-operation terms that must never appear in CEO output
_SYSTEM_WORDS_RE = re.compile(
    r"系統健康|系統運作|資料可信度|延遲|P95|雜訊清除|健康狀態|"
    r"pipeline|ingestion|資料完整率|traffic_light|success_rate|"
    r"latency_p95|noise_filtered",
    re.IGNORECASE,
)

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
    r"了當前的重要產業趨勢",
    r"與.*相關的技術或概念",
]
_HOMEWORK_RE = re.compile("|".join(_HOMEWORK_PATTERNS), re.IGNORECASE)

# Keywords suggesting a curated/index/archive page rather than a single event
_INDEX_PAGE_KEYWORDS = [
    "curated list", "curated", "rss feed", "rss", "entries for the year",
    "collecting", "archive", "overview", "stats", "year in review",
    "changelog", "release notes list", "index of", "table of contents",
    "as part of its mission", "newsletter",
]

# Patterns that indicate non-event content (title/summary level)
_NON_EVENT_TITLE_PREFIXES = [
    r"^as part of its mission",
    r"^overview",
    r"^archive",
    r"^curated",
    r"^table of contents",
    r"^newsletter",
    r"^a list of",
    r"^collection of",
]
_NON_EVENT_TITLE_RE = re.compile(
    "|".join(_NON_EVENT_TITLE_PREFIXES), re.IGNORECASE
)

# Event action verbs (Chinese + English) — at least one must appear for event
_EVENT_VERBS_RE = re.compile(
    r"推出|收購|禁用|發布|漏洞|裁員|上調|下調|立法|訴訟|"
    r"投資|併購|開源|下架|召回|罰款|合併|關閉|暫停|擴張|"
    r"launch|acquir|ban|releas|vulnerabilit|layoff|raise|lower|"
    r"legislat|sued|invest|merg|open.?source|remov|recall|fine|"
    r"shut|suspend|expand|block|hide|filter|maintain|operat|"
    r"announc|introduc|report|discover|breach|hack|leak|"
    r"partner|fund|grant|approve|reject|delay|cancel|"
    r"happen|develop|reveal|confirm|warn|impact|affect|change",
    re.IGNORECASE,
)

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


# ---------------------------------------------------------------------------
# Sanitization — grammar-safe, never creates broken sentences
# ---------------------------------------------------------------------------


def sanitize(text: str) -> str:
    """Remove banned words, then strip homework/template sentences.

    Fragment-replacement approach: replaces matched phrases with [...] instead
    of deleting entire sentences, then repairs broken Chinese grammar.
    """
    if not text:
        return ""
    result = text
    for bw in BANNED_WORDS:
        result = result.replace(bw, "")
    # Remove system operation terms
    result = _SYSTEM_WORDS_RE.sub("", result)
    # Replace homework fragments with gap marker instead of deleting
    result = _HOMEWORK_RE.sub("", result).strip()
    # Chinese grammar repair: fix dangling connectors at sentence start
    result = re.sub(r"^[了而並且因此所以但是然而]+", "", result)
    result = re.sub(r"(?<=[。！？\n])[了而並且因此所以但是然而]+", "", result)
    # Fix broken punctuation
    result = re.sub(r"。。+", "。", result)
    result = re.sub(r"[「」『』]\s*[「」『』]", "", result)
    result = re.sub(r"^\s*[，。；：]+", "", result)
    result = re.sub(r"\s{2,}", " ", result).strip()
    # If result is empty or too short after cleanup, return gap indicator
    if len(result) < 3:
        return ""
    return result


def responsible_party(category: str) -> str:
    """Map category to responsible party."""
    cat = (category or "").strip()
    if cat in RESPONSIBILITY_MAP:
        return RESPONSIBILITY_MAP[cat]
    cat_lower = cat.lower()
    for key, val in RESPONSIBILITY_MAP.items():
        if key.lower() in cat_lower or cat_lower in key.lower():
            return val
    return "策略長/PM"


# ---------------------------------------------------------------------------
# Non-event / index page detection
# ---------------------------------------------------------------------------


def is_index_page(card: EduNewsCard) -> bool:
    """Detect if a card represents a curated/index/archive page, not a single event."""
    combined = f"{card.title_plain} {card.what_happened}".lower()
    hits = sum(1 for kw in _INDEX_PAGE_KEYWORDS if kw in combined)
    return hits >= 1


def is_non_event_or_index(card: EduNewsCard) -> bool:
    """Strong filter: detect index pages AND content lacking event substance.

    Returns True (= should be excluded from CEO deck) if:
    - Title starts with known non-event prefixes, OR
    - Content is an index/archive page, OR
    - Content lacks 2+ of the 3 event elements: subject, action, time
    """
    title = (card.title_plain or "").strip()
    summary = (card.what_happened or "").strip()
    one_liner = getattr(card, "one_liner", "") or ""
    combined = f"{title} {summary} {one_liner}"

    # Check title prefixes
    if _NON_EVENT_TITLE_RE.search(title):
        return True

    # Check index page keywords
    if is_index_page(card):
        return True

    # Check event substance: subject + action + time
    has_action = bool(_EVENT_VERBS_RE.search(combined))
    has_time = bool(re.search(
        r"20\d{2}|本週|昨日|今日|近日|日前|上週|本月|今年|yesterday|today|"
        r"this week|last week|recently|Monday|Tuesday|Wednesday|Thursday|"
        r"Friday|Q[1-4]|January|February|March|April|May|June|July|"
        r"August|September|October|November|December",
        combined, re.IGNORECASE,
    ))
    # Subject: any recognizable proper noun (capitalized word >=3 chars)
    has_subject = bool(re.search(r"[A-Z][a-z]{2,}", combined))

    elements = sum([has_action, has_time, has_subject])
    # Allow missing 1 element, but not 2+
    if elements <= 1:
        return True

    return False


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
    "file", "files", "page", "pages", "link", "links", "site",
    "image", "images", "text", "click", "view", "read", "list",
    "item", "items", "type", "name", "code", "line", "lines",
    "source", "content", "title", "post", "blog", "web",
    "major", "latest", "recent", "global", "full", "total",
    "million", "billion", "percent", "number", "version",
    # Additional generic words that are NOT technical terms
    "mission", "preserve", "maintained", "traces", "hide",
    "videos", "operating", "operates", "capture", "snapshots",
    "increasingly", "becoming", "unarchivable", "part",
}

_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")

# ---------------------------------------------------------------------------
# Curated term dictionary (CEO-readable, white-language)
# ---------------------------------------------------------------------------
TERM_DICTIONARY: dict[str, dict[str, str]] = {
    # Internet / Web
    "internet archive": {
        "what": "一個非營利組織，專門保存網頁、書籍、影片的歷史副本",
        "biz": "若公司網站內容被保存，可能暴露舊版頁面或已修正的資訊",
    },
    "ublock origin": {
        "what": "一款免費的瀏覽器廣告攔截外掛，可過濾網頁廣告和追蹤器",
        "biz": "若用戶大量使用，會直接減少公司線上廣告的觸及率和收入",
    },
    "filter list": {
        "what": "一份規則清單，告訴攔截工具哪些網頁元素要隱藏或封鎖",
        "biz": "新的過濾規則可能影響公司產品頁面的顯示或廣告投放效果",
    },
    "youtube shorts": {
        "what": "YouTube 的短影片功能，類似 TikTok 的直式短影片",
        "biz": "短影片是目前社群行銷主力管道，若被過濾會影響觸及率",
    },
    "web crawler": {
        "what": "自動瀏覽網頁並下載內容的程式，搜尋引擎就是靠它收集資料",
        "biz": "爬蟲政策影響公司網站的 SEO 排名和資料被第三方使用的方式",
    },
    "webpage snapshot": {
        "what": "某個時間點的網頁完整備份，像是幫網頁拍照存檔",
        "biz": "舊版網頁快照可能被用於法律舉證或競爭對手情報分析",
    },
    "open source": {
        "what": "程式碼公開、任何人都能檢視和修改的軟體開發模式",
        "biz": "採用開源可降低授權成本，但需評估維護責任和安全風險",
    },
    "api": {
        "what": "不同軟體之間溝通的標準介面，像是餐廳的點餐窗口",
        "biz": "API 品質直接影響產品整合速度和合作夥伴的接入體驗",
    },
    "saas": {
        "what": "透過網路訂閱使用的軟體服務，不需自己安裝維護",
        "biz": "SaaS 模式影響公司的 IT 支出結構和資料控制權",
    },
    "ai": {
        "what": "人工智慧，讓電腦模擬人類思考和決策的技術",
        "biz": "AI 工具可提升效率但需評估準確性、成本和合規風險",
    },
    "llm": {
        "what": "大型語言模型，能理解和生成人類語言的 AI 系統",
        "biz": "可用於客服、內容生成、資料分析，但有幻覺和隱私風險",
    },
    "blockchain": {
        "what": "一種分散式帳本技術，資料一旦寫入就很難竄改",
        "biz": "可能影響供應鏈追溯、數位資產管理和跨境支付流程",
    },
    "cloud": {
        "what": "透過網路使用遠端伺服器的運算和儲存資源",
        "biz": "雲端成本和供應商鎖定是 IT 策略的核心決策點",
    },
    "cybersecurity": {
        "what": "保護電腦系統和資料不被未經授權存取或攻擊的措施",
        "biz": "資安事件可導致營運中斷、罰款和商譽損失",
    },
    "zero-day": {
        "what": "軟體中尚未被修補的安全漏洞，攻擊者可能已在利用",
        "biz": "零日漏洞代表最高風險等級，需立即評估受影響系統",
    },
    "ransomware": {
        "what": "會加密你的檔案並要求付贖金才解鎖的惡意程式",
        "biz": "勒索軟體可癱瘓整個營運，備份和應變計畫是關鍵防線",
    },
    "gdpr": {
        "what": "歐盟的個人資料保護法規，對資料收集和使用有嚴格規範",
        "biz": "違規罰款最高達全球營收 4%，影響所有有歐洲用戶的業務",
    },
    "iot": {
        "what": "物聯網，讓日常設備（感測器、家電等）連上網路互相溝通",
        "biz": "IoT 設備增加攻擊面，但也帶來自動化和數據收集機會",
    },
    "edge computing": {
        "what": "把運算放在靠近資料來源的地方處理，而非全送到雲端",
        "biz": "可降低延遲和頻寬成本，適合即時應用場景",
    },
    "kubernetes": {
        "what": "自動管理大量容器化應用程式的開源平台",
        "biz": "降低維運人力但學習門檻高，是雲端架構的核心技術選擇",
    },
    "docker": {
        "what": "把應用程式和所需環境打包成標準化容器的工具",
        "biz": "加速部署和環境一致性，是現代軟體交付的基礎設施",
    },
    "microservices": {
        "what": "把大系統拆成多個獨立小服務，各自開發部署",
        "biz": "提升開發速度但增加系統複雜度，需權衡團隊規模",
    },
    "devops": {
        "what": "開發和維運團隊緊密合作、自動化交付的工作方式",
        "biz": "DevOps 成熟度直接影響產品上線速度和系統穩定性",
    },
    "fintech": {
        "what": "運用科技改善金融服務的產業，如行動支付、線上借貸",
        "biz": "FinTech 競爭者可能侵蝕傳統金融業務的市場份額",
    },
    "quantum computing": {
        "what": "利用量子力學原理進行運算的新型電腦，部分問題可指數加速",
        "biz": "長期可能破解現有加密，短期影響有限但需開始規劃",
    },
    "5g": {
        "what": "第五代行動通訊技術，速度更快、延遲更低、連接更多設備",
        "biz": "5G 基礎建設影響遠端辦公、智慧工廠和新產品開發",
    },
    "ar": {
        "what": "擴增實境，在現實世界上疊加數位資訊的技術",
        "biz": "AR 可用於培訓、產品展示和遠端維修，降低實體成本",
    },
    "vr": {
        "what": "虛擬實境，用頭戴裝置沈浸在完全數位化的環境中",
        "biz": "VR 應用於培訓和協作，但硬體成本和使用者接受度是門檻",
    },
    "nft": {
        "what": "非同質化代幣，用區塊鏈證明數位物品的獨特性和所有權",
        "biz": "NFT 熱潮已降溫，但底層技術仍可用於數位資產認證",
    },
    "web3": {
        "what": "基於區塊鏈的去中心化網路願景，用戶擁有自己的數據",
        "biz": "Web3 概念尚在早期，投資需謹慎評估實際商業價值",
    },
    "semiconductor": {
        "what": "半導體，製造晶片的核心材料，驅動所有電子設備",
        "biz": "晶片供應影響產品交期和成本，是供應鏈的戰略性物資",
    },
    "x86-64": {
        "what": "個人電腦和伺服器最常用的處理器架構標準",
        "biz": "x86 生態系決定軟體相容性，架構轉移影響 IT 採購決策",
    },
    "arm": {
        "what": "一種低功耗處理器架構，廣泛用於手機和新型筆電",
        "biz": "ARM 架構在伺服器和筆電的崛起影響軟體開發和採購策略",
    },
    "gpu": {
        "what": "圖形處理器，擅長大量並行運算，是 AI 訓練的核心硬體",
        "biz": "GPU 供需和價格直接影響 AI 專案的成本和可行性",
    },
    "bandwidth": {
        "what": "網路一次能傳輸多少資料的上限，像是水管的粗細",
        "biz": "頻寬不足會影響雲端服務品質和遠端辦公體驗",
    },
    "latency": {
        "what": "從發出請求到收到回應的等待時間",
        "biz": "高延遲影響用戶體驗和即時交易系統的可靠性",
    },
    "encryption": {
        "what": "把資料轉換成只有授權者才能讀取的密碼形式",
        "biz": "加密是資料保護的基礎，法規常要求傳輸和儲存都要加密",
    },
    "vpn": {
        "what": "虛擬私人網路，在公共網路上建立加密的私人通道",
        "biz": "VPN 是遠端辦公的安全基礎設施，但也可能被用來繞過管制",
    },
    "cdn": {
        "what": "內容分發網路，把網站內容複製到全球各地的伺服器加速存取",
        "biz": "CDN 影響網站速度和全球用戶體驗，是數位業務的基礎設施",
    },
    "machine learning": {
        "what": "讓電腦從資料中自動學習規律和做預測的技術",
        "biz": "ML 可應用於推薦系統、風控、預測分析，但需要高品質資料",
    },
    "deep learning": {
        "what": "機器學習的進階版，用多層神經網路處理複雜模式",
        "biz": "深度學習驅動圖像辨識、語音助理等產品，但訓練成本高",
    },
    "autonomous driving": {
        "what": "自動駕駛，讓車輛不需人類操控就能行駛的技術",
        "biz": "自駕技術影響物流成本、保險模式和法規合規需求",
    },
    "sustainability": {
        "what": "在滿足當前需求的同時，不損害未來世代滿足需求的能力",
        "biz": "ESG 和永續報告已成為投資人和監管機構的硬性要求",
    },
}


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
# Context-aware term explainer with curated dictionary
# ---------------------------------------------------------------------------

from schemas.education_models import TERM_METAPHORS as _CURATED_TERMS


def _lookup_term(term: str) -> dict[str, str] | None:
    """Look up a term in the curated dictionary (case-insensitive, fuzzy)."""
    low = term.lower()
    # Exact match
    if low in TERM_DICTIONARY:
        return TERM_DICTIONARY[low]
    # Substring match (e.g., "uBlock" matches "ublock origin")
    for key, val in TERM_DICTIONARY.items():
        if low in key or key in low:
            return val
    return None


def build_term_explainer(card: EduNewsCard) -> list[dict[str, str]]:
    """Build context-aware term explanations for CEO audience.

    Returns list of dicts: [{"term": ..., "explain": ...}, ...]
    Uses curated dictionary first, then TERM_METAPHORS, then gap indicator.
    Never produces "與...相關的技術或概念" template.
    """
    terms = extract_key_terms(card)
    if not terms:
        return []

    results: list[dict[str, str]] = []
    for term in terms[:4]:
        low = term.lower()

        # 1) Check our curated CEO dictionary first
        curated_dict = _lookup_term(term)
        if curated_dict:
            explain = f"{curated_dict['what']}。對公司的關係：{curated_dict['biz']}"
            results.append({"term": term, "explain": explain})
            continue

        # 2) Check education TERM_METAPHORS
        metaphor = None
        for key, val in _CURATED_TERMS.items():
            if low == key.lower() or low in key.lower():
                metaphor = val
                break
        if metaphor:
            results.append({"term": term, "explain": metaphor})
            continue

        # 3) Gap indicator — never produce empty-talk template
        results.append({
            "term": term,
            "explain": "目前資料缺口：此名詞在來源中未提供足夠上下文，無法可靠解釋。",
        })

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
        known_facts: list of 3 known facts
        why_it_matters: Why this matters (2-3 bullet points)
        possible_impact: list of 2-3 possible impacts
        what_to_do: Concrete next step (1-2 items, no homework)
        quote: Key evidence quote from the source
        sources: list of source URLs
        owner: responsible party
    """
    dc = build_decision_card(card)

    # headline — prefer title, truncated sensibly (word boundary for English)
    raw_title = sanitize(card.title_plain or "事件摘要")
    if len(raw_title) > 30:
        # Try to cut at word boundary
        cut = raw_title[:30]
        last_space = cut.rfind(" ")
        headline = cut[:last_space] if last_space > 15 else cut
    else:
        headline = raw_title

    # one_liner — what happened in one sentence (≤22 chars, rewrite not truncate)
    if card.what_happened:
        raw_one = sanitize(card.what_happened)
        if len(raw_one) > 80:
            cut = raw_one[:80]
            last_period = max(cut.rfind("。"), cut.rfind(". "), cut.rfind("，"))
            one_liner = cut[:last_period + 1] if last_period > 30 else cut
        else:
            one_liner = raw_one
    else:
        one_liner = dc["event"]

    # known_facts — from decision card
    known_facts = dc["facts"][:3]

    # why_it_matters — combine effects + risks into prose (2-3 points)
    why_parts: list[str] = []
    if card.why_important:
        cleaned = sanitize(card.why_important[:150])
        if cleaned and len(cleaned) > 10:
            why_parts.append(cleaned)
    for eff in dc["effects"]:
        if not eff.startswith("缺口") and eff not in why_parts:
            why_parts.append(eff)
    if not why_parts:
        why_parts.append("影響面待進一步分析")

    # possible_impact — from effects + risks
    impacts: list[str] = []
    for eff in dc["effects"]:
        if not eff.startswith("缺口"):
            impacts.append(eff)
    for risk in dc["risks"]:
        if not risk.startswith("缺口"):
            impacts.append(f"風險：{risk}")
    if not impacts:
        impacts.append("缺口：影響面尚待分析")

    # what_to_do — concrete action, never homework
    actions: list[str] = []
    for a in dc["actions"]:
        actions.append(f"{a}（負責人：{dc['owner']}）")
    if not actions:
        actions.append(f"待確認下一步（負責人：{dc['owner']}）")

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
        "known_facts": known_facts,
        "why_it_matters": why_parts[:3],
        "possible_impact": impacts[:3],
        "what_to_do": actions[:2],
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
