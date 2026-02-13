"""Z5 — Education Renderer（教育版轉譯器）。

把 Z4 產出的深度分析（結構化資料或純文本 fallback）轉譯成
「成人教育版」報告：淺白但不幼稚、技術可理解、可直接貼到 Notion、
可做成 PPT、可匯入 XMind。

兩種輸入模式：
- 模式 A（優先）：直接傳入 MergedResult 列表 + DeepAnalysisReport + metrics dict
- 模式 B（fallback）：讀取 outputs/deep_analysis.md + outputs/metrics.json

輸出四份檔案：
1. docs/reports/deep_analysis_education_version.md       （Notion 主版）
2. docs/reports/deep_analysis_education_version_ppt.md   （PPT 切頁版）
3. docs/reports/deep_analysis_education_version_xmind.md （XMind 階層大綱）
4. outputs/deep_analysis_education.md                     （pipeline 輸出副本）
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from schemas.education_models import (
    TERM_METAPHORS,
    EduNewsCard,
    SystemHealthReport,
    is_invalid_item,
    translate_fail_reason,
)
from schemas.models import DeepAnalysisReport, ItemDeepDive, MergedResult
from utils.logger import get_logger

# ---------------------------------------------------------------------------
# 成人版比喻庫（正式但親切）
# ---------------------------------------------------------------------------

_METAPHOR_BANK: dict[str, list[str]] = {
    "科技/技術": [
        "類似企業內部導入新 ERP 系統——前期陣痛期長，但一旦上線效率顯著提升",
        "可以想成一款新作業系統的大版本更新：功能更多，但相容性需要時間磨合",
    ],
    "創業/投融資": [
        "就像一家新創公司完成 A 輪募資：手上有了資金，但也背負了對投資人的交付承諾",
        "類比成一位自由工作者拿到第一份企業長約——收入穩定了，但彈性也縮小了",
    ],
    "人工智慧": [
        "可以類比為替整個部門聘了一位不休息的助理——產出量暴增，但品質仍需人工把關",
        "就像導入自動化產線：效率提升，但既有流程和人力配置都得重新設計",
    ],
    "政策/監管": [
        "類似某個行業突然多了一條新法規——企業必須在期限內完成合規調整",
        "可以想成租屋市場出了新的管制條例：房東、房客、仲介三方都受影響",
    ],
    "氣候/能源": [
        "就像一棟老大樓要做節能改造：短期花錢，長期省下的能源成本更可觀",
        "類比成從燃油車換成電動車——使用習慣要改，但長期營運成本下降",
    ],
    "金融/財經": [
        "就像央行調整利率——看似只是一個數字的變動，但會連鎖影響房貸、消費、投資決策",
        "類比成公司季報公布後的股價反應：數字本身重要，但市場的「預期落差」更關鍵",
    ],
    "併購/企業": [
        "就像兩家公司合併部門——表面上是資源整合，實際上涉及團隊文化磨合與客戶遷移",
        "可以類比成連鎖品牌收購獨立店家：產品線擴大，但原有品牌個性可能被稀釋",
    ],
    "消費電子": [
        "類似旗艦手機發表會——產品本身重要，但更值得關注的是它對供應鏈與競品的連鎖反應",
    ],
    "遊戲/娛樂": [
        "就像一款遊戲大改版本：核心玩家的反應決定了後續用戶留存率",
    ],
}

_DEFAULT_METAPHORS = [
    "可以想成一項新政策或新產品的發布——本身有直接影響，但更值得觀察的是它引發的連鎖反應",
    "就像公司內部發了一封全員公告——訊息本身不長，但後續的組織調整才是重點",
]

# ---------------------------------------------------------------------------
# 比喻選擇
# ---------------------------------------------------------------------------


def _pick_metaphor(category: str, title: str, idx: int = 0) -> str:
    """依分類 + index 挑選成人可接受的比喻。"""
    bank = _METAPHOR_BANK.get(category, _DEFAULT_METAPHORS)
    title_lower = title.lower()
    if any(kw in title_lower for kw in ("收購", "acquire", "merger", "併購", "买")):
        bank = _METAPHOR_BANK.get("併購/企業", bank)
    return bank[idx % len(bank)]


# ---------------------------------------------------------------------------
# 術語白話轉譯
# ---------------------------------------------------------------------------


def _translate_term(text: str) -> str:
    """把文字中的抽象術語加上括號解釋（第一次出現才加）。"""
    result = text
    for term, explanation in TERM_METAPHORS.items():
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        if pattern.search(result):
            short_exp = explanation.split("：", 1)[-1] if "：" in explanation else explanation
            result = pattern.sub(f"{term}（{short_exp}）", result, count=1)
    return result


# ---------------------------------------------------------------------------
# 模式 A：結構化輸入 → 成人教育版卡片
# ---------------------------------------------------------------------------


def _build_card_from_structured(
    result: MergedResult,
    dive: ItemDeepDive | None,
    idx: int,
) -> EduNewsCard:
    """從結構化的 MergedResult + ItemDeepDive 建立成人教育版卡片。"""
    a, b = result.schema_a, result.schema_b
    body_text = a.summary_zh or ""
    title = a.title_zh or a.source_id or result.item_id

    combined_text = f"{title} {body_text}"
    if is_invalid_item(combined_text):
        return _build_invalid_card(result.item_id, body_text, a.category or "", a.source_id,
                                   dive.signal_strength if dive else 0.0, b.final_score)

    # === 正常新聞卡片 ===
    # 事實核對
    confirmed: list[str] = []
    unverified: list[str] = []
    if dive and dive.core_facts:
        for fact in dive.core_facts[:5]:
            confirmed.append(fact)
    if not confirmed:
        if a.key_points:
            confirmed = a.key_points[:3]

    # 證據
    evidence_lines: list[str] = []
    if dive and dive.evidence_excerpts:
        for excerpt in dive.evidence_excerpts[:4]:
            evidence_lines.append(f"原文：「{excerpt}」")
            evidence_lines.append(f"→ 繁中說明：{_explain_excerpt(excerpt)}")
    elif dive and dive.core_facts:
        for fact in dive.core_facts[:3]:
            evidence_lines.append(f"事實：「{fact}」")

    # 技術/商業解讀
    tech_interp = _build_technical_interpretation(dive, a.category, title, a.key_points)

    # 二階效應
    derivable = dive.derivable_effects[:4] if dive and dive.derivable_effects else []
    speculative = dive.speculative_effects[:3] if dive and dive.speculative_effects else []
    obs_metrics = dive.observation_metrics[:4] if dive and dive.observation_metrics else []

    if not derivable and dive and dive.derivable_effects:
        derivable = dive.derivable_effects[:2]
    if not obs_metrics and derivable:
        obs_metrics = [f"觀察 {d[:20]} 的後續發展" for d in derivable[:2]]

    # 行動建議
    actions = _build_action_items(dive, a.category, title)

    # 媒體素材
    img_sugs = _build_image_suggestions(a.category, title)
    vid_sugs = _build_video_suggestions(title, a.category)
    read_sugs = _build_reading_suggestions(title, a.category)

    return EduNewsCard(
        item_id=result.item_id,
        is_valid_news=True,
        title_plain=_clean_title(title),
        what_happened=_make_what_happened(a.key_points, title),
        why_important=_make_why_important(dive, a.category, title),
        focus_action=_make_focus_action(dive, a.category),
        metaphor=_pick_metaphor(a.category or "", title, idx),
        fact_check_confirmed=confirmed,
        fact_check_unverified=unverified if unverified else ["（本次資料範圍內無需額外驗證的項目）"],
        evidence_lines=evidence_lines,
        technical_interpretation=tech_interp,
        derivable_effects=derivable,
        speculative_effects=speculative,
        observation_metrics=obs_metrics,
        action_items=actions[:3],
        image_suggestions=img_sugs,
        video_suggestions=vid_sugs,
        reading_suggestions=read_sugs,
        source_url=a.source_id if a.source_id.startswith("http") else "（缺）",
        category=a.category or "綜合資訊",
        signal_strength=dive.signal_strength if dive else 0.0,
        final_score=b.final_score,
        source_name=a.source_id,
    )


def _build_invalid_card(
    item_id: str, body_text: str, category: str, source_id: str,
    signal_strength: float, final_score: float,
) -> EduNewsCard:
    """建立無效內容卡片。"""
    return EduNewsCard(
        item_id=item_id,
        is_valid_news=False,
        invalid_reason="此項目並非有效新聞內容。系統在抓取過程中，將網站的系統提示（如登入頁面、Session 過期通知）誤判為新聞。",
        title_plain="⚠️ 非新聞內容（系統訊息）",
        what_happened="資料抓取程式（Scraper）在存取目標網站時，並未取得實際的新聞內容，而是擷取到網站的系統提示頁面。",
        why_important="辨識「什麼不是新聞」是資訊素養的基礎能力。這類雜訊在自動化資料收集中很常見，認識它有助於正確解讀報告。",
        invalid_cause="blocked / extract_low_quality — 目標網站要求登入或觸發了反爬蟲機制",
        invalid_fix="可調整抓取策略（如更換 User-Agent、增加重試邏輯）或將該來源加入排除清單",
        evidence_lines=[
            f"原文片段：「{body_text[:120]}」",
            "→ 繁中說明：這些文字屬於網站的 Session 管理提示，不包含任何新聞資訊。",
        ],
        source_url="（缺）",
        category=category or "不適用",
        signal_strength=signal_strength,
        final_score=final_score,
        source_name=source_id,
    )


def _clean_title(title: str) -> str:
    """清理標題。"""
    title = re.sub(r"[\[\]【】「」『』]", "", title)
    if len(title) > 60:
        title = title[:58] + "…"
    return title


def _explain_excerpt(excerpt: str) -> str:
    """為原文片段提供繁中解釋。"""
    text = excerpt.strip()
    if len(text) > 100:
        text = text[:98] + "…"
    return f"此段原文表明：{text}"


def _make_what_happened(key_points: list[str], title: str) -> str:
    if key_points:
        first = key_points[0]
        if len(first) > 100:
            first = first[:98] + "…"
        return first
    return f"主題摘要：{title[:60]}"


def _make_why_important(dive: ItemDeepDive | None, category: str, title: str) -> str:
    if dive and dive.derivable_effects:
        first = dive.derivable_effects[0]
        if len(first) > 100:
            first = first[:98] + "…"
        return f"此事件的潛在影響：{first}"
    return f"這是 {category or '綜合'} 領域的重要動態，可能對相關產業或使用者產生連鎖影響。"


def _make_focus_action(dive: ItemDeepDive | None, category: str) -> str:
    if dive and dive.observation_metrics:
        return f"建議持續關注：{dive.observation_metrics[0]}"
    return f"建議關注 {category or '此領域'} 後續的官方公告或市場回應。"


def _build_technical_interpretation(
    dive: ItemDeepDive | None, category: str, title: str, key_points: list[str],
) -> str:
    """建立 120-220 字的技術/商業解讀。"""
    parts: list[str] = []

    if dive and dive.first_principles:
        parts.append(_translate_term(dive.first_principles[:200]))
    elif dive and dive.forces_incentives:
        parts.append(_translate_term(dive.forces_incentives[:200]))

    if dive and dive.event_breakdown:
        parts.append(_translate_term(dive.event_breakdown[:150]))

    if not parts:
        # fallback：用 key_points 組成解讀
        cat_label = category or "綜合"
        kp_text = "；".join(key_points[:3]) if key_points else title[:40]
        parts.append(
            f"本事件涉及 {cat_label} 領域。核心要點包括：{kp_text}。"
            f"從產業鏈角度來看，這類事件通常會影響上下游的合作關係與競爭格局，"
            f"值得持續追蹤後續的市場反應與政策回應。"
        )

    result = " ".join(parts)
    if len(result) < 120:
        result += f" 綜合來看，此事件在 {category or '該領域'} 具有指標性意義，後續發展方向取決於各方利害關係人的回應速度與策略調整。"
    if len(result) > 250:
        result = result[:248] + "…"
    return result


def _build_action_items(dive: ItemDeepDive | None, category: str, title: str) -> list[str]:
    """建立可執行行動（含動作 + 產出物 + 期限建議）。"""
    actions: list[str] = []
    if dive and dive.opportunities:
        for opp in dive.opportunities[:3]:
            clean = opp[:80] if len(opp) > 80 else opp
            actions.append(f"本週內：{clean} → 產出：初步評估筆記")
    if not actions:
        title_short = title[:20]
        actions = [
            f"本週內：搜尋「{title_short}」的最新報導，確認事件進展 → 產出：摘要筆記",
            "兩週內：評估此事件對自身工作或投資的潛在影響 → 產出：風險/機會清單",
        ]
    return actions[:3]


def _build_image_suggestions(category: str, title: str) -> list[str]:
    cat_map = {
        "科技/技術": "科技產業示意圖",
        "氣候/能源": "能源轉型概念圖",
        "金融/財經": "財經數據趨勢圖",
        "人工智慧": "AI 技術概念圖",
        "政策/監管": "法規政策流程圖",
        "併購/企業": "企業併購關係圖",
    }
    img_type = cat_map.get(category, "產業趨勢示意圖")
    title_short = title[:15]
    return [
        f"🖼️ {img_type}｜關鍵字：{category or '趨勢'} {title_short}｜用途：PPT 封面或 Notion 配圖",
        f"🖼️ 資訊圖表（Infographic）｜關鍵字：{title_short} 數據視覺化｜用途：社群分享",
    ]


def _build_video_suggestions(title: str, category: str) -> list[str]:
    title_short = re.sub(r"[^\w\s\u4e00-\u9fff]", "", title)[:20].strip()
    cat_short = (category or "科技").replace("/", " ")
    return [
        f"🎬 YouTube 搜尋：「{title_short} {cat_short} 分析解讀」",
        f"🎬 YouTube 搜尋：「{cat_short} 趨勢 2025 中文」",
    ]


def _build_reading_suggestions(title: str, category: str) -> list[str]:
    title_short = re.sub(r"[^\w\s\u4e00-\u9fff]", "", title)[:20].strip()
    return [
        f"📎 Google 搜尋：「{title_short} 產業分析」",
        f"📎 Google 搜尋：「{category or '科技'} 最新動態 {datetime.now().year}」",
    ]


# ---------------------------------------------------------------------------
# 模式 B：文本 fallback 解析
# ---------------------------------------------------------------------------


def _parse_deep_analysis_text(text: str) -> list[dict[str, Any]]:
    """從 deep_analysis.md 文本穩健解析出每則新聞的基本結構。"""
    items: list[dict[str, Any]] = []
    sections = re.split(r"^### \d+\.\s*", text, flags=re.MULTILINE)

    for section in sections[1:]:
        item: dict[str, Any] = {}
        lines = section.strip().split("\n")
        if lines:
            item["item_id"] = lines[0].strip()[:30]

        core_facts: list[str] = []
        in_facts = False
        for line in lines:
            if "核心事實" in line:
                in_facts = True
                continue
            if in_facts:
                if line.startswith("- "):
                    core_facts.append(line[2:].strip())
                elif line.startswith("**") or line.startswith("####"):
                    in_facts = False
        item["core_facts"] = core_facts

        evidence: list[str] = []
        for line in lines:
            if line.startswith("> "):
                excerpt = line[2:].strip().strip('"').strip("「」")
                if excerpt and len(excerpt) > 5:
                    evidence.append(excerpt)
        item["evidence_excerpts"] = evidence

        for line in lines:
            m = re.search(r"信號強度:\s*([\d.]+)", line)
            if m:
                item["signal_strength"] = float(m.group(1))
                break

        if item.get("item_id"):
            items.append(item)
    return items


def _parse_metrics_dict(metrics: dict[str, Any]) -> SystemHealthReport:
    return SystemHealthReport(
        success_rate=float(metrics.get("enrich_success_rate", 0)),
        p50_latency=float(metrics.get("enrich_latency_p50", 0)),
        p95_latency=float(metrics.get("enrich_latency_p95", 0)),
        entity_noise_removed=int(metrics.get("entity_noise_removed", 0)),
        total_runtime=float(metrics.get("total_runtime_seconds", 0)),
        run_id=str(metrics.get("run_id", "unknown")),
        fail_reasons=dict(metrics.get("enrich_fail_reasons", {})),
    )


def _build_card_from_parsed(parsed: dict[str, Any], idx: int) -> EduNewsCard:
    """從 fallback 解析結果建立成人教育版卡片。"""
    item_id = parsed.get("item_id", f"item_{idx}")
    core_facts = parsed.get("core_facts", [])
    evidence = parsed.get("evidence_excerpts", [])

    combined = " ".join(core_facts + evidence)
    if is_invalid_item(combined):
        return EduNewsCard(
            item_id=item_id,
            is_valid_news=False,
            invalid_reason="系統提示訊息，非真實新聞內容",
            title_plain="⚠️ 非新聞內容（系統訊息）",
            what_happened="資料抓取過程中擷取到的系統提示頁面，並非新聞。",
            why_important="辨識無效內容是使用自動化工具的必備技能。",
            invalid_cause="抓取目標回傳非預期內容（登入頁面或錯誤頁面）",
            invalid_fix="調整抓取策略或將來源加入排除清單",
            evidence_lines=[f"原文片段：「{combined[:100]}」", "→ 繁中說明：此為系統自動產生的提示文字。"],
            source_url="（缺）",
            signal_strength=parsed.get("signal_strength", 0.0),
        )

    title = core_facts[0] if core_facts else item_id
    evidence_lines = []
    for e in evidence[:4]:
        evidence_lines.append(f"原文：「{e}」")
        evidence_lines.append(f"→ 繁中說明：此段原文表明：{e[:60]}")

    return EduNewsCard(
        item_id=item_id,
        is_valid_news=True,
        title_plain=_clean_title(title),
        what_happened=title if len(title) <= 100 else title[:98] + "…",
        why_important="此事件反映了當前的重要產業趨勢，值得持續觀察後續發展。",
        focus_action="建議追蹤相關後續報導與官方聲明。",
        metaphor=_DEFAULT_METAPHORS[idx % len(_DEFAULT_METAPHORS)],
        fact_check_confirmed=core_facts[:3] if core_facts else ["（資料不足）"],
        fact_check_unverified=["需交叉比對其他來源以確認"],
        evidence_lines=evidence_lines,
        technical_interpretation=f"根據現有資料，此事件涉及 {title[:30]} 相關領域。由於僅從文本 fallback 解析，解讀深度有限，建議參考原始分析報告（outputs/deep_analysis.md）取得完整脈絡。",
        action_items=[
            f"本週內：搜尋「{title[:15]}」最新報導 → 產出：摘要筆記",
            "兩週內：評估此事件與自身業務的關聯性 → 產出：影響評估表",
        ],
        image_suggestions=["🖼️ 新聞示意圖｜關鍵字：產業趨勢 新聞｜用途：簡報配圖"],
        video_suggestions=[f"🎬 YouTube 搜尋：「{title[:15]} 分析」"],
        reading_suggestions=[f"📎 Google 搜尋：「{title[:15]} 產業分析」"],
        source_url="（缺）",
        signal_strength=parsed.get("signal_strength", 0.0),
    )


# ---------------------------------------------------------------------------
# Notion 主版渲染（成人教育版）
# ---------------------------------------------------------------------------


_FILTER_REASON_LABELS: dict[str, str] = {
    "too_old": "時間過舊",
    "lang_not_allowed": "語言不符",
    "keyword_mismatch": "關鍵字不符",
    "body_too_short": "內文過短",
}


def _render_notion_md(
    cards: list[EduNewsCard],
    health: SystemHealthReport,
    report_time: str,
    total_items: int,
    metrics: dict[str, Any],
    filter_summary: dict[str, Any] | None = None,
) -> str:
    lines: list[str] = []
    valid_count = sum(1 for c in cards if c.is_valid_news)
    invalid_count = len(cards) - valid_count

    # ===== A. 封面區 =====
    fail_lines_brief = []
    for reason, count in health.fail_reasons.items():
        fail_lines_brief.append(f"{translate_fail_reason(reason)}（{count} 次）")

    lines.extend([
        "# 🤖 AI 深度情報分析報告 — 成人教學版",
        "",
        "> 本報告將「分析師版」的深度分析結果，轉譯為不需要技術背景即可理解的版本。",
        "> 適合：產品經理、投資人、管理層、或任何對科技趨勢好奇的讀者。",
        "",
        "## 📋 封面資訊",
        "",
        "| 項目 | 內容 |",
        "|------|------|",
        f"| 報告時間 | {report_time} |",
        f"| Run ID | `{health.run_id}` |",
        f"| 分析項目數 | {total_items} 則（有效 {valid_count}、無效 {invalid_count}）|",
        f"| 全文抓取成功率（Enrich Success Rate） | {health.success_rate:.0f}% |",
        f"| 總執行時間 | {health.total_runtime:.1f} 秒 |",
        f"| 主要失敗原因 | {'; '.join(fail_lines_brief) if fail_lines_brief else '無'} |",
        "",
        "**📖 閱讀指南：**",
        "",
        "1. **先看**：「今日結論」— 2 分鐘掌握全貌",
        "2. **再看**：「每則新聞卡片」— 深入了解每則事件的背景與影響",
        "3. **最後看**：「Metrics 與運維建議」— 了解系統狀態與改善方向",
        "",
        "---",
        "",
    ])

    # ===== B. 今日結論 =====
    valid_cards = [c for c in cards if c.is_valid_news]
    sources = set()
    topics = set()
    for c in valid_cards:
        if c.source_name:
            sources.add(c.source_name[:20])
        if c.category:
            topics.add(c.category)

    lines.extend([
        "## 📊 今日結論（Executive Summary）",
        "",
        f"本次分析共處理 {total_items} 則資料項目，其中 **{valid_count} 則為有效新聞**"
        + (f"、**{invalid_count} 則為無效內容**（系統訊息或抓取失敗）" if invalid_count else "")
        + "。",
        "",
    ])

    if valid_cards:
        titles_brief = "、".join(c.title_plain[:15] for c in valid_cards[:3])
        lines.append(f"有效新聞涵蓋的主題包括：{titles_brief}。")
    lines.append("")

    # 可信度評估
    if health.success_rate >= 80:
        reliability = "本批次資料可信度**良好**，大部分項目成功完成全文抽取與分析。"
    elif health.success_rate >= 50:
        reliability = "本批次資料可信度**中等**，約有一半以上項目成功處理，但仍有改善空間。"
    else:
        reliability = "本批次資料可信度**偏低**，多數項目在抓取或分析階段遭遇問題，建議檢查來源與網路狀態。"
    lines.extend([
        reliability,
        "",
        "| 指標 | 數值 |",
        "|------|------|",
        f"| 有效項目 | {valid_count} 則 |",
        f"| 無效項目 | {invalid_count} 則 |",
        f"| 主要來源 | {', '.join(list(sources)[:3]) if sources else '（未記錄）'} |",
        f"| 主要主題 | {', '.join(list(topics)[:3]) if topics else '（未分類）'} |",
        "",
        "---",
        "",
    ])

    # ===== C. 系統說明 QA =====
    lines.extend([
        "## ❓ 這套系統到底在做什麼（QA）",
        "",
        "**Q1：這份報告的輸入是什麼？**",
        "",
        "系統從多個 RSS 來源（如 TechCrunch、36kr、Hacker News）自動擷取最新文章。每篇文章經過全文抓取後，成為一個「資料項目（Item）」，也就是本報告的分析對象。",
        "",
        "**Q2：輸出是什麼？**",
        "",
        "系統產出四種文件：① `digest.md`（快速摘要）、② `deep_analysis.md`（分析師版深度報告）、③ 本份教育版報告、④ 選配的通知推送（Slack/飛書/Email）。每份文件服務不同讀者。",
        "",
        "**Q3：什麼是 Pipeline（資料處理管線）？**",
        "",
        "Pipeline 就像一座「資料工廠」的生產線。原始新聞從入口進來，依序經過清洗、分類、評分、深度分析等站點，最終產出結構化的報告。每個站點專責一項任務，如果某站出錯，不會影響其他站的運作。在資料工程領域，這種模式稱為 ETL（Extract-Transform-Load），是最常見的自動化資料處理架構。",
        "",
        "**Q4：為什麼要打分數？分數代表什麼？**",
        "",
        "系統會為每則新聞計算一個綜合分數（final_score），考量新穎性、實用性、熱度、可行性等維度。只有分數超過門檻（預設 7.0）的項目才會進入深度分析階段。這個機制稱為「品質門檻（Quality Gate）」，目的是把有限的運算資源集中在最有價值的內容上。",
        "",
        "**Q5：為什麼會出現「不是新聞的字串」？**",
        "",
        "自動化抓取時，部分網站會要求登入、顯示 Cookie 通知、或回傳 Session 過期的提示頁面。這些頁面會被抓取程式當成文章內容。本報告中，這類項目會被標記為「⚠️ 非新聞內容」，並提供具體的修復建議。",
        "",
        "**Q6：我今天要做的最小動作是什麼？**",
        "",
        "1. 花 2 分鐘讀完「今日結論」",
        "2. 挑 1 則你最感興趣的新聞卡片仔細閱讀",
        "3. 按照卡片中的「可執行行動」完成 1 個任務",
        "",
        "---",
        "",
    ])

    # ===== D. 系統流程圖 =====
    lines.extend([
        "## 🗺️ 系統流程圖",
        "",
        "```mermaid",
        "flowchart LR",
        "    A[📡 RSS 來源] --> B[Z1 資料擷取]",
        "    B --> C[去重複 & 過濾]",
        "    C --> D[Z2 AI 分析核心]",
        "    D --> E[Z3 儲存 & 基礎報告]",
        "    E --> F[Z4 深度分析]",
        "    F --> G[Z5 教育版轉譯]",
        "    G --> H[📤 輸出 & 通知]",
        "```",
        "",
        "**各站說明：**",
        "",
        "- **Z1 資料擷取（Ingestion）**：從 RSS 來源抓取文章，進行全文擷取與基本清洗。白話說：「把網路上的原始新聞抓下來」。",
        "- **Z2 AI 分析核心（AI Core）**：對每則新聞做摘要、分類、評分、實體抽取。白話說：「讓 AI 讀完每篇文章並寫重點」。",
        "- **Z3 儲存與交付（Storage & Delivery）**：將結果存入資料庫，生成摘要報告。白話說：「把成績記錄下來並寄出成績單」。",
        "- **Z4 深度分析（Deep Analyzer）**：對通過品質門檻的項目做七維深度分析。白話說：「對優秀的文章做進階研究報告」。",
        "- **Z5 教育版轉譯（Education Renderer）**：就是產出本報告的環節，把技術語言轉成易懂版本。白話說：「把研究報告翻譯成白話文」。",
        "",
        "---",
        "",
    ])

    # ===== E. 今日新聞卡片 =====
    lines.extend([
        "## 📰 今日新聞卡片",
        "",
    ])

    if not cards and filter_summary:
        # Render empty-report section with filter breakdown
        input_count = filter_summary.get("input_count", 0)
        reasons = filter_summary.get("dropped_by_reason", {})
        lines.extend([
            "## 📭 本次無有效新聞",
            "",
            f"本次 pipeline 共擷取 {input_count} 則原始項目，但全數未通過篩選。",
            "",
            "| 篩選原因 | 被刷掉數量 |",
            "|----------|-----------|",
        ])
        for reason_key, count in reasons.items():
            label = _FILTER_REASON_LABELS.get(reason_key, reason_key)
            lines.append(f"| {label} | {count} |")
        if not reasons:
            lines.append("| （無詳細原因） | 0 |")
        lines.extend([
            "",
            '> 💡 若要以較寬鬆門檻重跑，請使用：`$env:RUN_PROFILE="calibration"; python scripts/run_once.py`',
            "",
            "---",
            "",
        ])

    for i, card in enumerate(cards, 1):
        if not card.is_valid_news:
            lines.extend(_render_invalid_card_adult(card, i))
        else:
            lines.extend(_render_valid_card_adult(card, i))
        lines.extend(["", "---", ""])

    # ===== F. Metrics 與運維 =====
    lines.extend(_render_metrics_section(health, metrics))

    lines.extend([
        "",
        "---",
        "",
        "*本報告由 AI Intel Education Renderer (Z5) 自動生成｜深度等級：adult*",
        "",
    ])

    return "\n".join(lines)


def _render_valid_card_adult(card: EduNewsCard, idx: int) -> list[str]:
    """渲染一張有效新聞的成人版卡片。"""
    lines = [
        f"### 第 {idx} 則：{card.title_plain}",
        "",
        "#### 摘要",
        "",
        f"- **發生了什麼：** {card.what_happened}",
        f"- **為什麼重要：** {card.why_important}",
        f"- **你要關注什麼：** {card.focus_action}",
        "",
    ]

    # 事實核對
    lines.append("#### 事實核對（Fact Check）")
    lines.append("")
    if card.fact_check_confirmed:
        for fact in card.fact_check_confirmed:
            lines.append(f"- ✅ {fact}")
    if card.fact_check_unverified:
        for item in card.fact_check_unverified:
            lines.append(f"- ⚠️ {item}")
    lines.append("")

    # 證據
    lines.append("#### 證據片段（Evidence Snippets）")
    lines.append("")
    if card.evidence_lines:
        for ev in card.evidence_lines:
            lines.append(f"- {ev}")
    else:
        lines.append("- （本則無可引用的原文片段）")
    lines.append("")

    # 技術/商業解讀
    lines.append("#### 技術/商業解讀")
    lines.append("")
    lines.append(card.technical_interpretation if card.technical_interpretation else "（資料不足，無法提供深度解讀）")
    lines.append("")
    if card.metaphor:
        lines.append(f"> 💡 **類比理解：** {card.metaphor}")
        lines.append("")

    # 二階效應
    lines.append("#### 二階效應（Second-order Effects）")
    lines.append("")
    lines.append("| 類型 | 影響 | 觀察指標 |")
    lines.append("|------|------|----------|")
    for i, eff in enumerate(card.derivable_effects):
        obs = card.observation_metrics[i] if i < len(card.observation_metrics) else "待定"
        lines.append(f"| 直接影響 | {eff} | {obs} |")
    for eff in card.speculative_effects:
        lines.append(f"| 間接影響（需觀察） | {eff} | 持續追蹤相關報導 |")
    if not card.derivable_effects and not card.speculative_effects:
        lines.append("| — | （資料不足） | — |")
    lines.append("")

    # 可執行行動
    lines.append("#### 可執行行動（Actions）")
    lines.append("")
    for act in card.action_items:
        lines.append(f"- {act}")
    lines.append("")

    # 媒體素材
    lines.append("#### 媒體與延伸資源")
    lines.append("")
    for img in card.image_suggestions:
        lines.append(f"- {img}")
    for vid in card.video_suggestions:
        lines.append(f"- {vid}")
    for rd in card.reading_suggestions:
        lines.append(f"- {rd}")
    lines.append(f"- 🔗 **原始連結：** {card.source_url}")

    return lines


def _render_invalid_card_adult(card: EduNewsCard, idx: int) -> list[str]:
    """渲染一張無效內容的成人版卡片。"""
    lines = [
        f"### 第 {idx} 則：⚠️ 無效內容",
        "",
        "> **判定：此項目為無效內容，並非真實新聞。**",
        "",
        f"**說明：** {card.invalid_reason}",
        "",
        f"**可能原因：** {card.invalid_cause if card.invalid_cause else '抓取目標回傳非預期內容（如登入頁面、404 錯誤）'}",
        "",
        f"**修復建議：** {card.invalid_fix if card.invalid_fix else '調整抓取策略或將此來源加入排除清單'}",
        "",
    ]
    if card.evidence_lines:
        lines.append("**原文片段：**")
        lines.append("")
        for ev in card.evidence_lines:
            lines.append(f"- {ev}")
        lines.append("")
    return lines


def _render_metrics_section(health: SystemHealthReport, metrics: dict[str, Any]) -> list[str]:
    """渲染 Metrics 與運維區塊。"""
    lines = [
        "## 📊 Metrics 與運維建議",
        "",
        "### 健康度儀表板",
        "",
        "| 指標 | 數值 | 解讀 | 建議門檻 |",
        "|------|------|------|----------|",
    ]

    # 成功率
    rate = health.success_rate
    if rate >= 80:
        interp = "良好：大部分項目成功處理"
    elif rate >= 50:
        interp = "一般：約半數項目處理成功，部分來源可能有問題"
    else:
        interp = "異常：多數項目處理失敗，需立即排查"
    lines.append(f"| Enrich Success Rate | {rate:.0f}% | {interp} | ≥ 80% |")

    # 失敗原因
    if health.fail_reasons:
        reasons_str = "; ".join(f"{translate_fail_reason(k)}×{v}" for k, v in health.fail_reasons.items())
        lines.append(f"| Top Fail Reasons | 見下 | {reasons_str} | 無失敗為最佳 |")

    # 延遲
    p50_interp = "正常" if health.p50_latency < 10 else ("偏慢" if health.p50_latency < 20 else "異常緩慢")
    lines.append(f"| Latency P50 | {health.p50_latency:.1f}s | {p50_interp} | < 10s |")
    p95_interp = "正常" if health.p95_latency < 20 else ("偏慢" if health.p95_latency < 40 else "異常緩慢")
    lines.append(f"| Latency P95 | {health.p95_latency:.1f}s | {p95_interp} | < 30s |")

    # 執行時間
    lines.append(f"| Total Runtime | {health.total_runtime:.1f}s | — | 依資料量而定 |")

    # 雜訊清除
    lines.append(f"| Entity Noise Removed | {health.entity_noise_removed} | {'已清除部分雜訊實體' if health.entity_noise_removed > 0 else '無需清除'} | — |")

    lines.extend(["", ""])

    # 紅黃綠燈
    lines.extend([
        f"### {health.traffic_light_emoji} 總體評估：{health.traffic_light_label}",
        "",
    ])

    # 排錯指令
    lines.extend([
        "### 排錯指引",
        "",
        "**🔍 快速：查看最近的錯誤 log**",
        "",
        "```powershell",
        "# PowerShell",
        'Select-String -Path ".\\logs\\app.log" -Pattern "ERROR|WARN" | Select-Object -Last 20',
        "```",
        "",
        "```bash",
        "# Bash",
        'grep -E "ERROR|WARN" logs/app.log | tail -20',
        "```",
        "",
        "**🔧 中等：篩選特定階段的 log**",
        "",
        "```powershell",
        '# 查 Z5 教育版相關',
        'Select-String -Path ".\\logs\\app.log" -Pattern "Z5|education|Education"',
        '# 查抓取失敗',
        'Select-String -Path ".\\logs\\app.log" -Pattern "enrich.*fail|blocked|timeout"',
        "```",
        "",
        "```bash",
        'grep -iE "Z5|education" logs/app.log',
        'grep -iE "enrich.*fail|blocked|timeout" logs/app.log',
        "```",
        "",
        "**🛠️ 深入：重跑或調整來源**",
        "",
        "```powershell",
        '# 關閉特定來源（在 .env 中修改 RSS_FEEDS_JSON）',
        '# 或調低品質門檻做測試',
        '# GATE_MIN_SCORE=5.0 python scripts\\run_once.py',
        "```",
        "",
    ])

    # 下一 Sprint 建議
    lines.extend([
        "### 下一 Sprint 建議",
        "",
        "1. **提高抓取成功率**：檢查 `core/ingestion.py` 中的重試邏輯與 User-Agent 設定",
        "2. **降低 P95 延遲**：在 `core/ai_core.py` 中增加連線池或並行處理",
        "3. **改善實體清洗**：擴充 `utils/entity_cleaner.py` 中的規則，減少 false positive",
        "4. **來源品質監控**：為每個 RSS 來源建立獨立的成功率追蹤（可在 `utils/metrics.py` 擴充）",
        "5. **教育版內容深度**：根據讀者回饋調整 `core/education_renderer.py` 中的解讀模板",
        "",
    ])

    return lines


# ---------------------------------------------------------------------------
# PPT 切頁版渲染（成人版：每頁最多 10 行）
# ---------------------------------------------------------------------------


def _render_ppt_md(
    cards: list[EduNewsCard],
    health: SystemHealthReport,
    report_time: str,
    total_items: int,
) -> str:
    pages: list[str] = []
    valid_count = sum(1 for c in cards if c.is_valid_news)
    invalid_count = len(cards) - valid_count

    # 封面頁
    pages.append(
        f"# 🤖 AI 深度情報分析報告\n\n"
        f"📅 {report_time}\n"
        f"📊 分析 {total_items} 則（有效 {valid_count}、無效 {invalid_count}）\n"
        f"✅ 成功率 {health.success_rate:.0f}% ｜ ⏱️ {health.total_runtime:.1f}s\n\n"
        f"{health.traffic_light_emoji} {health.traffic_light_label}"
    )

    # 今日結論頁
    valid_cards = [c for c in cards if c.is_valid_news]
    titles = "、".join(c.title_plain[:12] for c in valid_cards[:3])
    pages.append(
        f"## 📊 今日結論\n\n"
        f"有效新聞 {valid_count} 則 ｜ 無效 {invalid_count} 則\n"
        f"主題涵蓋：{titles if titles else '無'}\n"
        f"資料可信度：{'良好' if health.success_rate >= 80 else '中等' if health.success_rate >= 50 else '偏低'}\n\n"
        f"{'→ 系統運作正常' if health.traffic_light == 'green' else '→ 部分項目需關注' if health.traffic_light == 'yellow' else '→ 建議排查異常'}"
    )

    # 流程圖頁
    pages.append(
        "## 🗺️ 系統流程\n\n"
        "```mermaid\n"
        "flowchart LR\n"
        "    A[RSS 來源] --> B[Z1 擷取]\n"
        "    B --> C[Z2 AI 分析]\n"
        "    C --> D[Z3 儲存]\n"
        "    D --> E[Z4 深度分析]\n"
        "    E --> F[Z5 教育版]\n"
        "```"
    )

    # 每則新聞 2-4 頁
    for i, card in enumerate(cards, 1):
        tag = "⚠️ 無效" if not card.is_valid_news else f"#{i}"

        if not card.is_valid_news:
            pages.append(
                f"## {tag}：非新聞內容\n\n"
                f"**判定：** 無效（系統訊息）\n"
                f"**原因：** {card.invalid_cause or '抓取失敗'}\n"
                f"**修復：** {card.invalid_fix or '調整抓取策略'}\n\n"
                f"→ 此項目不含有效新聞資訊"
            )
            continue

        # 頁 1：摘要
        what = card.what_happened[:80]
        why = card.why_important[:80]
        focus = card.focus_action[:80]
        pages.append(
            f"## {tag}：{card.title_plain[:30]}\n\n"
            f"**發生了什麼：** {what}\n\n"
            f"**為什麼重要：** {why}\n\n"
            f"**關注重點：** {focus}"
        )

        # 頁 2：事實 + 證據
        facts_text = "\n".join(f"- ✅ {f[:60]}" for f in card.fact_check_confirmed[:3])
        ev_text = "\n".join(f"- {e[:70]}" for e in card.evidence_lines[:3])
        pages.append(
            f"## {tag}（續）事實核對 & 證據\n\n"
            f"**事實核對：**\n{facts_text}\n\n"
            f"**證據片段：**\n{ev_text}"
        )

        # 頁 3：行動 + 風險
        act_text = "\n".join(f"- {a[:70]}" for a in card.action_items[:3])
        effects_text = "\n".join(f"- {e[:60]}" for e in card.derivable_effects[:2])
        pages.append(
            f"## {tag}（續）行動建議 & 影響\n\n"
            f"**可執行行動：**\n{act_text}\n\n"
            f"**直接影響：**\n{effects_text if effects_text else '- （資料不足）'}"
        )

    # Metrics 頁
    pages.append(
        f"## 📊 系統效能\n\n"
        f"| 指標 | 數值 |\n"
        f"|------|------|\n"
        f"| 成功率 | {health.success_rate:.0f}% |\n"
        f"| P50 延遲 | {health.p50_latency:.1f}s |\n"
        f"| P95 延遲 | {health.p95_latency:.1f}s |\n"
        f"| 雜訊清除 | {health.entity_noise_removed} 個 |\n\n"
        f"{health.traffic_light_emoji} {health.traffic_light_label}"
    )

    return "\n\n---\n\n".join(pages) + "\n"


# ---------------------------------------------------------------------------
# XMind 階層大綱版
# ---------------------------------------------------------------------------


def _render_xmind_md(
    cards: list[EduNewsCard],
    health: SystemHealthReport,
    report_time: str,
    total_items: int,
) -> str:
    """渲染 XMind 友善的純階層大綱（2 空格縮排）。"""
    lines: list[str] = []
    date_str = report_time.split(" ")[0] if " " in report_time else report_time

    lines.append(f"AI 深度情報分析（{date_str}）")
    lines.append("  今日結論")
    valid_count = sum(1 for c in cards if c.is_valid_news)
    invalid_count = len(cards) - valid_count
    lines.append(f"    有效新聞 {valid_count} 則")
    lines.append(f"    無效內容 {invalid_count} 則")
    lines.append(f"    成功率 {health.success_rate:.0f}%")
    lines.append(f"    健康度 {health.traffic_light_label}")

    lines.append("  系統流程（Z1-Z5）")
    lines.append("    Z1 資料擷取")
    lines.append("    Z2 AI 分析核心")
    lines.append("    Z3 儲存與交付")
    lines.append("    Z4 深度分析")
    lines.append("    Z5 教育版轉譯")

    for i, card in enumerate(cards, 1):
        title = card.title_plain[:30]
        if not card.is_valid_news:
            lines.append(f"  無效項目 {i}：{title}")
            lines.append("    判定：無效內容")
            lines.append(f"    原因：{card.invalid_cause or '抓取失敗'}")
            lines.append(f"    修復：{card.invalid_fix or '調整策略'}")
            continue

        lines.append(f"  新聞 {i}：{title}")
        lines.append("    摘要")
        lines.append(f"      發生了什麼：{card.what_happened[:50]}")
        lines.append(f"      為什麼重要：{card.why_important[:50]}")
        lines.append(f"      關注重點：{card.focus_action[:50]}")

        lines.append("    事實核對")
        for fact in card.fact_check_confirmed[:3]:
            lines.append(f"      ✅ {fact[:50]}")
        for uv in card.fact_check_unverified[:2]:
            lines.append(f"      ⚠️ {uv[:50]}")

        lines.append("    證據")
        for ev in card.evidence_lines[:3]:
            lines.append(f"      {ev[:60]}")

        lines.append("    技術解讀")
        if card.technical_interpretation:
            # 分成多行
            interp = card.technical_interpretation[:120]
            lines.append(f"      {interp}")

        lines.append("    二階效應")
        for eff in card.derivable_effects[:2]:
            lines.append(f"      直接：{eff[:50]}")
        for eff in card.speculative_effects[:2]:
            lines.append(f"      間接：{eff[:50]}")

        lines.append("    行動建議")
        for act in card.action_items[:3]:
            lines.append(f"      {act[:60]}")

        lines.append("    素材")
        for img in card.image_suggestions[:1]:
            lines.append(f"      {img[:60]}")
        for vid in card.video_suggestions[:1]:
            lines.append(f"      {vid[:60]}")

    lines.append("  Metrics & 運維")
    lines.append(f"    成功率 {health.success_rate:.0f}%")
    lines.append(f"    P50 延遲 {health.p50_latency:.1f}s")
    lines.append(f"    P95 延遲 {health.p95_latency:.1f}s")
    lines.append(f"    雜訊清除 {health.entity_noise_removed} 個")
    lines.append(f"    健康度 {health.traffic_light_emoji} {health.traffic_light_label}")
    if health.fail_reasons:
        lines.append("    失敗原因")
        for reason, count in health.fail_reasons.items():
            lines.append(f"      {translate_fail_reason(reason)} ×{count}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def render_education_report(
    results: list[MergedResult] | None = None,
    report: DeepAnalysisReport | None = None,
    metrics: dict[str, Any] | None = None,
    deep_analysis_text: str | None = None,
    max_items: int = 0,
    filter_summary: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    """Z5 主入口：生成成人教育版報告。

    模式 A（優先）：傳入 results + report + metrics
    模式 B（fallback）：傳入 deep_analysis_text + metrics

    回傳 (notion_md, ppt_md, xmind_md) 三份 Markdown 字串。
    """
    log = get_logger()
    log.info("--- Z5: Education Renderer 開始 ---")

    now = datetime.now().strftime("%Y-%m-%d %H:%M 本機時區")

    if metrics is None:
        metrics = {}
    health = _parse_metrics_dict(metrics)
    total_items = int(metrics.get("total_items", 0))

    cards: list[EduNewsCard] = []

    # 模式 A：結構化輸入
    if results is not None and report is not None:
        log.info("Z5: 使用模式 A（結構化輸入），%d 則結果", len(results))
        dive_map: dict[str, ItemDeepDive] = {}
        for dive in report.per_item_analysis:
            dive_map[dive.item_id] = dive

        for i, r in enumerate(results):
            dive = dive_map.get(r.item_id)
            card = _build_card_from_structured(r, dive, i)
            cards.append(card)

        if report.generated_at:
            now = report.generated_at
        if report.total_items:
            total_items = report.total_items

    # 模式 B：文本 fallback
    elif deep_analysis_text:
        log.info("Z5: 使用模式 B（文本 fallback）")
        parsed_items = _parse_deep_analysis_text(deep_analysis_text)
        for i, parsed in enumerate(parsed_items):
            card = _build_card_from_parsed(parsed, i)
            cards.append(card)
        total_items = total_items or len(cards)

    else:
        log.warning("Z5: 沒有可用的輸入資料，生成空白報告")
        total_items = 0

    if max_items > 0 and len(cards) > max_items:
        cards = cards[:max_items]
        log.info("Z5: 限制最大項目數為 %d", max_items)

    log.info("Z5: 共生成 %d 張教育版卡片", len(cards))

    notion_md = _render_notion_md(cards, health, now, total_items, metrics, filter_summary=filter_summary)
    ppt_md = _render_ppt_md(cards, health, now, total_items)
    xmind_md = _render_xmind_md(cards, health, now, total_items)

    log.info("--- Z5: Education Renderer 完成 ---")
    return notion_md, ppt_md, xmind_md


def write_education_reports(
    notion_md: str,
    ppt_md: str,
    xmind_md: str = "",
    project_root: Path | None = None,
) -> tuple[Path, Path, Path, Path]:
    """寫入四份教育版報告檔案。

    回傳 (notion_path, ppt_path, xmind_path, outputs_path)。
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent

    docs_dir = project_root / "docs" / "reports"
    outputs_dir = project_root / "outputs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    notion_path = docs_dir / "deep_analysis_education_version.md"
    ppt_path = docs_dir / "deep_analysis_education_version_ppt.md"
    xmind_path = docs_dir / "deep_analysis_education_version_xmind.md"
    outputs_path = outputs_dir / "deep_analysis_education.md"

    notion_path.write_text(notion_md, encoding="utf-8")
    ppt_path.write_text(ppt_md, encoding="utf-8")
    xmind_path.write_text(xmind_md, encoding="utf-8")
    outputs_path.write_text(notion_md, encoding="utf-8")

    log = get_logger()
    log.info("Z5: 教育版報告已寫入：%s, %s, %s, %s", notion_path, ppt_path, xmind_path, outputs_path)

    return notion_path, ppt_path, xmind_path, outputs_path


def render_error_report(error: Exception) -> str:
    """Z5 失敗時，生成一份「教育版生成失敗說明」。"""
    import traceback
    tb = traceback.format_exception(type(error), error, error.__traceback__)
    tb_summary = "".join(tb[-3:]) if len(tb) > 3 else "".join(tb)

    return (
        "# ⚠️ 教育版報告生成失敗\n"
        "\n"
        f"**錯誤訊息：** `{error}`\n"
        "\n"
        "## 錯誤追蹤（Traceback 摘要）\n"
        "\n"
        "```\n"
        f"{tb_summary}"
        "```\n"
        "\n"
        "## 修復步驟\n"
        "\n"
        "1. 檢查 `outputs/deep_analysis.md` 是否存在且格式正確\n"
        "2. 檢查 `outputs/metrics.json` 是否存在且為有效 JSON\n"
        "3. 查看 `logs/app.log` 中的 Z5 相關錯誤訊息\n"
        "4. 確認 `.env` 中 `EDU_REPORT_ENABLED=true`\n"
        "5. 確認 Python 版本 ≥ 3.10（本模組使用 `X | Y` 型別語法）\n"
        "\n"
        "**快速排查指令：**\n"
        "\n"
        "```powershell\n"
        '# PowerShell\n'
        'Select-String -Path ".\\logs\\app.log" -Pattern "Z5|education" | Select-Object -Last 10\n'
        "```\n"
        "\n"
        "```bash\n"
        '# Bash\n'
        'grep -i "Z5\\|education" logs/app.log | tail -10\n'
        "```\n"
        "\n"
        "若仍無法解決，請將上述 Traceback 與 log 提供給工程師。\n"
    )
