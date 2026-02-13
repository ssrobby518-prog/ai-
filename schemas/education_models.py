"""Z5 教育版報告的中介資料模型。

定義結構化的教育版卡片、封面資訊、系統健康度等資料結構，
供 education_renderer 使用。與專案既有風格一致，使用 dataclass。

支援兩種深度等級：
- adult（預設）：成人教育版，適合 0 基礎但有大學教育背景的成人
- teen：青少年版（保留但不預設輸出）
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 非新聞內容偵測用的關鍵片段
# ---------------------------------------------------------------------------

SYSTEM_BANNER_PATTERNS: list[str] = [
    "you signed in",
    "you signed out",
    "you switched accounts",
    "reload to refresh your session",
    "cookie",
    "accept all cookies",
    "sign in to",
    "log in to",
    "dismiss alert",
    "copy file name to clipboard",
    "display the source diff",
    "display the rich diff",
    "lines changed",
    "expand all lines",
]


def is_system_banner(text: str) -> bool:
    """判斷文字是否為系統提示/登入橫幅等非新聞內容。"""
    lower = text.lower()
    hit_count = sum(1 for p in SYSTEM_BANNER_PATTERNS if p in lower)
    # 命中 2 個以上模式，或全文太短且命中 1 個
    if hit_count >= 2:
        return True
    if hit_count >= 1 and len(text.strip()) < 200:
        return True
    return False


def is_invalid_item(text: str) -> bool:
    """判斷內容是否為無效新聞（含 system banner + 過短且無資訊）。

    規則：
    1. 命中 system banner 模式 → 無效
    2. 內容 < 80 字且缺少可辨識的中英文名詞/動詞 → 無效
    """
    if is_system_banner(text):
        return True
    stripped = text.strip()
    if len(stripped) < 80:
        # 簡易 heuristic：至少要有 2 個以上的中文字詞或英文單詞（長度 > 3）
        import re
        cjk_words = re.findall(r'[\u4e00-\u9fff]{2,}', stripped)
        en_words = re.findall(r'[a-zA-Z]{4,}', stripped)
        if len(cjk_words) + len(en_words) < 2:
            return True
    return False


# ---------------------------------------------------------------------------
# 單則新聞教育版卡片（成人版擴充欄位）
# ---------------------------------------------------------------------------


@dataclass
class EduNewsCard:
    """教育版中「每則新聞」的卡片結構。"""

    item_id: str = ""
    is_valid_news: bool = True
    invalid_reason: str = ""

    # 基本摘要
    title_plain: str = ""
    what_happened: str = ""
    why_important: str = ""
    focus_action: str = ""       # 你要關注什麼（行動導向）
    metaphor: str = ""

    # 事實核對
    fact_check_confirmed: list[str] = field(default_factory=list)
    fact_check_unverified: list[str] = field(default_factory=list)

    # 證據
    evidence_lines: list[str] = field(default_factory=list)

    # 技術/商業解讀（成人版核心，120-220 字）
    technical_interpretation: str = ""

    # 二階效應
    derivable_effects: list[str] = field(default_factory=list)
    speculative_effects: list[str] = field(default_factory=list)
    observation_metrics: list[str] = field(default_factory=list)

    # 可執行行動（最多 3 條，含動作+產出物+期限）
    action_items: list[str] = field(default_factory=list)

    # 媒體素材占位
    image_suggestions: list[str] = field(default_factory=list)
    video_suggestions: list[str] = field(default_factory=list)
    reading_suggestions: list[str] = field(default_factory=list)
    source_url: str = ""

    # 無效內容專用
    invalid_cause: str = ""
    invalid_fix: str = ""

    # Z4 原始資料
    category: str = ""
    signal_strength: float = 0.0
    final_score: float = 0.0
    source_name: str = ""


# ---------------------------------------------------------------------------
# 系統健康度（紅黃綠燈）
# ---------------------------------------------------------------------------


@dataclass
class SystemHealthReport:
    """pipeline 健康度報告。"""

    success_rate: float = 0.0
    p50_latency: float = 0.0
    p95_latency: float = 0.0
    entity_noise_removed: int = 0
    total_runtime: float = 0.0
    run_id: str = ""
    fail_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def traffic_light(self) -> str:
        """根據規則回傳紅黃綠燈。

        規則（定義於 spec）：
        - 綠燈：success_rate >= 80% 且 p95 < 30s
        - 黃燈：success_rate >= 50% 或 p95 < 60s
        - 紅燈：其餘
        """
        if self.success_rate >= 80 and self.p95_latency < 30:
            return "green"
        if self.success_rate >= 50 or self.p95_latency < 60:
            return "yellow"
        return "red"

    @property
    def traffic_light_emoji(self) -> str:
        m = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
        return m.get(self.traffic_light, "⚪")

    @property
    def traffic_light_label(self) -> str:
        m = {
            "green": "系統健康，資料品質良好",
            "yellow": "部分異常，但整體仍可使用",
            "red": "多項指標異常，建議排查",
        }
        return m.get(self.traffic_light, "未知")


# ---------------------------------------------------------------------------
# 失敗原因翻譯（技術 -> 人話）
# ---------------------------------------------------------------------------

FAIL_REASON_TRANSLATIONS: dict[str, str] = {
    "extract_low_quality": "全文抽取品質不足（網頁內容過少或格式特殊，無法擷取完整文章）",
    "blocked": "被目標網站封鎖（對方的反爬蟲機制阻止了我們的存取）",
    "connection_error": "網路連線失敗（可能是 DNS 解析錯誤、連線逾時或伺服器無回應）",
    "timeout": "請求逾時（目標伺服器回應過慢，超過設定的等待上限）",
    "parse_error": "內容解析失敗（網頁結構不符合預期，可能是改版或動態載入）",
    "rate_limited": "請求頻率過高被限速（短時間內發出太多請求，被伺服器暫時封鎖）",
    "404": "頁面不存在（文章可能已刪除或 URL 已失效）",
    "encoding_error": "文字編碼錯誤（原始網頁的字元編碼宣告不正確，導致亂碼）",
}


def translate_fail_reason(reason: str) -> str:
    """把技術錯誤碼翻譯成易懂的中文。"""
    return FAIL_REASON_TRANSLATIONS.get(reason, f"其他原因（{reason}）")


# ---------------------------------------------------------------------------
# 術語翻譯（抽象 -> 白話，成人可接受的精確度）
# ---------------------------------------------------------------------------

TERM_METAPHORS: dict[str, str] = {
    "adoption curve": "技術採用曲線：新產品從早期使用者擴散到大眾的過程，就像新 App 從科技圈擴散到你爸媽手機裡",
    "scalability": "可擴展性：系統能否在用戶暴增時仍正常運作，就像餐廳從 10 桌擴到 100 桌、廚房是否跟得上",
    "stakeholders": "利害關係人：所有會被這件事影響到的人或組織",
    "pipeline": "資料處理管線：一套自動化的資料處理流程，原始資料從頭進去、成品從尾端出來",
    "enrichment": "資料增補：從外部來源補充更多背景資訊，讓原始資料更完整",
    "quality gate": "品質門檻：只有達標的資料才會進入下一階段，用來過濾低品質內容",
    "signal strength": "信號強度：衡量一則資訊的可信度與重要性的綜合指標",
    "evidence density": "證據密度：文章中有多少可驗證的事實與數據支撐",
    "latency": "回應延遲：從發出請求到收到回應所花的時間",
    "entity": "命名實體：文本中可識別的專有名詞，如人名、公司名、地名",
    "dedup": "去重複：移除重複的資料項目，確保每則新聞只出現一次",
    "metrics": "效能指標：用來衡量系統運作狀態的數據",
    "interoperability": "互通性：不同系統之間能否順利交換資料與協作",
    "regulatory leverage": "監管槓桿：政府透過法規對產業施加影響的力道",
    "incentive design": "誘因設計：透過獎懲機制引導特定行為的策略",
    "supply chain": "供應鏈：產品從原料到消費者手中的整條價值傳遞路徑",
    "security boundary": "安全邊界：系統中劃定的信任與不信任區域的分界線",
    "first principles": "第一性原理：從最基本的事實出發進行推理，不依賴類比或經驗",
}
