"""XMind 總經理版心智圖生成器。

輸出 outputs/mindmap.xmind（XMind 2020 zip 格式）。
結構：content.json + metadata.json，可被 XMind 正常開啟。
三大分支：今日重點 / 每則新聞（6子節點） / 系統健康（5子節點）。

禁用詞彙：ai捕捉、AI Intel、Z1~Z5、pipeline、ETL、verify_run、ingestion、ai_core
"""

from __future__ import annotations

import json
import uuid
import zipfile
from pathlib import Path

from schemas.education_models import EduNewsCard, SystemHealthReport, translate_fail_reason
from utils.logger import get_logger

# ---------------------------------------------------------------------------
# XMind marker constants (built-in marker IDs)
# ---------------------------------------------------------------------------
_MARKERS = {
    "high": "priority-1",
    "medium": "priority-2",
    "low": "priority-3",
    "star": "star-red",
    "check": "task-done",
    "flag": "flag-red",
    "warn": "symbol-attention",
    "info": "symbol-info",
}


def _uid() -> str:
    return str(uuid.uuid4()).replace("-", "")[:24]


def _topic(
    title: str,
    children: list[dict] | None = None,
    labels: list[str] | None = None,
    markers: list[str] | None = None,
    note: str | None = None,
    summary: str | None = None,
) -> dict:
    """Build a rich XMind topic node with labels, markers, notes, summaries."""
    t: dict = {"id": _uid(), "title": title}
    if children:
        t["children"] = {"attached": children}
    if labels:
        t["labels"] = labels
    if markers:
        t["markers"] = [{"markerId": m} for m in markers]
    if note:
        t["notes"] = {"plain": {"content": note}}
    if summary:
        t["summaries"] = [{"id": _uid(), "topicId": _uid(), "title": summary}]
    return t


# ---------------------------------------------------------------------------
# Branch 1: 今日重點 (executive overview — 6 fixed sub-nodes)
# ---------------------------------------------------------------------------

def _build_highlights_branch(
    cards: list[EduNewsCard],
    health: SystemHealthReport,
    total_items: int,
) -> dict:
    valid = [c for c in cards if c.is_valid_news]

    # 1. 市場變化
    market_items = []
    for c in valid[:3]:
        effects = c.derivable_effects[:2] if c.derivable_effects else ["待評估"]
        market_items.append(_topic(
            f"{c.title_plain[:30]}",
            children=[_topic(e[:60]) for e in effects],
            labels=["市場動態"],
        ))
    if not market_items:
        market_items = [_topic("本日無顯著市場變化", labels=["無資料"])]
    market = _topic("市場變化", market_items, markers=[_MARKERS["info"]],
                    note="涵蓋今日所有與市場供需、定價、份額相關的變動。")

    # 2. 競爭動態
    compete_items = []
    for c in valid[:3]:
        compete_items.append(_topic(
            f"{c.title_plain[:25]} — 競爭面影響",
            labels=[c.category or "綜合"],
            note=f"事件概要：{c.what_happened[:100]}",
        ))
    if not compete_items:
        compete_items = [_topic("本日無明確競爭情報")]
    compete = _topic("競爭動態", compete_items, markers=[_MARKERS["flag"]])

    # 3. 潛在商機
    opportunity_items = []
    for c in valid[:3]:
        opp = c.focus_action[:80] if c.focus_action else "持續觀察"
        opportunity_items.append(_topic(opp, labels=["商機線索"],
                                        note=f"來源：{c.source_name or '未知'}"))
    if not opportunity_items:
        opportunity_items = [_topic("本日未發現明確商機")]
    opportunity = _topic("潛在商機", opportunity_items, markers=[_MARKERS["star"]])

    # 4. 成本影響
    cost_items = []
    for c in valid[:2]:
        risk = c.speculative_effects[0][:60] if c.speculative_effects else "待評估"
        cost_items.append(_topic(
            f"{c.title_plain[:25]} → {risk}",
            labels=["成本"],
        ))
    if not cost_items:
        cost_items = [_topic("本日無重大成本衝擊")]
    cost = _topic("成本影響", cost_items, markers=[_MARKERS["warn"]])

    # 5. 時間敏感度
    urgency_items = []
    for c in valid[:3]:
        score_label = "高" if c.final_score >= 8.0 else "中" if c.final_score >= 6.0 else "低"
        urgency_items.append(_topic(
            f"[{score_label}] {c.title_plain[:30]}",
            labels=[f"評分 {c.final_score:.1f}"],
            markers=[_MARKERS["high"] if score_label == "高"
                     else _MARKERS["medium"] if score_label == "中"
                     else _MARKERS["low"]],
        ))
    if not urgency_items:
        urgency_items = [_topic("本日無緊急項目")]
    urgency = _topic("時間敏感度", urgency_items)

    # 6. 管理層需關注
    mgmt_items = [
        _topic(f"共 {total_items} 則情報，{len(valid)} 則有效", labels=["統計"]),
        _topic(f"資料完整率 {health.success_rate:.0f}%",
               markers=[_MARKERS["check"] if health.success_rate >= 80 else _MARKERS["warn"]]),
        _topic(f"系統狀態：{health.traffic_light_label}",
               labels=[health.traffic_light_emoji]),
    ]
    for c in valid[:2]:
        if c.action_items:
            mgmt_items.append(_topic(
                f"待決策：{c.action_items[0][:50]}",
                markers=[_MARKERS["flag"]],
                labels=[c.title_plain[:20]],
            ))
    mgmt = _topic("管理層需關注", mgmt_items, markers=[_MARKERS["star"]],
                   summary=f"本日 {len(valid)} 則有效新聞需管理層審閱")

    return _topic("今日重點", [market, compete, opportunity, cost, urgency, mgmt],
                  labels=["Executive Summary"],
                  markers=[_MARKERS["star"]])


# ---------------------------------------------------------------------------
# Branch 2: 每則新聞 (6 sub-nodes per card)
# ---------------------------------------------------------------------------

def _build_news_branch(cards: list[EduNewsCard]) -> dict:
    valid = [c for c in cards if c.is_valid_news]
    invalid = [c for c in cards if not c.is_valid_news]

    news_children: list[dict] = []

    for i, card in enumerate(valid, 1):
        # 6 sub-nodes per card
        what = _topic(f"發生什麼事：{card.what_happened[:100]}",
                      labels=["事實"], markers=[_MARKERS["info"]],
                      note=card.what_happened)
        why = _topic(f"為何重要：{card.why_important[:100]}",
                     labels=["分析"], markers=[_MARKERS["warn"]],
                     note=card.why_important)

        # 對公司影響
        effects = card.derivable_effects[:3] if card.derivable_effects else ["待評估"]
        impact_children = [_topic(e[:60], labels=["影響"]) for e in effects]
        impact = _topic("對公司影響", impact_children, markers=[_MARKERS["flag"]])

        # 主要風險
        risks = card.speculative_effects[:3] if card.speculative_effects else ["低風險"]
        risk_children = [_topic(r[:60], labels=["風險"]) for r in risks]
        risk = _topic("主要風險", risk_children, markers=[_MARKERS["warn"]])

        # 建議行動
        actions = card.action_items[:3] if card.action_items else ["持續觀察"]
        act_children = [_topic(a[:60], labels=["行動"]) for a in actions]
        action = _topic("建議行動", act_children, markers=[_MARKERS["check"]])

        # 需要誰決策
        stakeholder_items = [
            _topic("產品負責人：評估對產品線影響", labels=["決策者"]),
            _topic("技術主管：確認技術可行性", labels=["決策者"]),
            _topic("業務端：評估客戶與市場反應", labels=["決策者"]),
        ]
        stakeholder = _topic("需要誰決策", stakeholder_items, markers=[_MARKERS["flag"]])

        # Facts
        facts_children = []
        if card.fact_check_confirmed:
            for f in card.fact_check_confirmed[:3]:
                facts_children.append(_topic(f"已確認：{f[:60]}", markers=[_MARKERS["check"]]))
        if card.fact_check_unverified:
            for u in card.fact_check_unverified[:2]:
                facts_children.append(_topic(f"待驗證：{u[:60]}", markers=[_MARKERS["warn"]]))
        if card.evidence_lines:
            for ev in card.evidence_lines[:2]:
                facts_children.append(_topic(f"證據：{ev[:60]}", labels=["原文"]))

        card_children = [what, why, impact, risk, action, stakeholder]
        if facts_children:
            card_children.append(_topic("事實與證據", facts_children))

        # Metaphor as note on the card topic
        card_note = card.metaphor[:120] if card.metaphor else None

        score_label = "高" if card.final_score >= 8.0 else "中" if card.final_score >= 6.0 else "低"
        news_children.append(_topic(
            f"新聞{i}：{card.title_plain[:35]}",
            children=card_children,
            labels=[card.category or "綜合", f"評分 {card.final_score:.1f}", f"優先級：{score_label}"],
            markers=[_MARKERS["high"] if score_label == "高"
                     else _MARKERS["medium"] if score_label == "中"
                     else _MARKERS["low"]],
            note=card_note,
            summary=f"{card.what_happened[:50]}",
        ))

    # Invalid items
    if invalid:
        inv_children = []
        for j, c in enumerate(invalid, 1):
            inv_children.append(_topic(
                f"無效項目 {j}：{c.title_plain[:30]}",
                children=[
                    _topic(f"原因：{c.invalid_cause or '資料異常'}"),
                    _topic(f"判定：{c.invalid_reason or '非新聞'}"),
                    _topic(f"修正建議：{c.invalid_fix or '調整來源'}"),
                ],
                labels=["已過濾"],
                markers=[_MARKERS["low"]],
            ))
        news_children.append(_topic(f"無效內容（{len(invalid)} 則）", inv_children))

    return _topic("新聞深度解析", news_children,
                  labels=[f"共 {len(valid)} 則有效"],
                  markers=[_MARKERS["info"]])


# ---------------------------------------------------------------------------
# Branch 3: 系統健康 (5 sub-nodes)
# ---------------------------------------------------------------------------

def _build_health_branch(health: SystemHealthReport) -> dict:
    # 1. 資料完整率
    rate_status = "良好" if health.success_rate >= 80 else "注意" if health.success_rate >= 50 else "異常"
    completeness = _topic(
        f"資料完整率：{health.success_rate:.0f}%（{rate_status}）",
        labels=["核心指標"],
        markers=[_MARKERS["check"] if health.success_rate >= 80 else _MARKERS["warn"]],
        note=f"成功率 {health.success_rate:.1f}%，低於 80% 需檢查資料來源是否異常。",
    )

    # 2. 處理速度
    speed_status = "正常" if health.p50_latency < 10 else "偏慢"
    speed = _topic(
        f"處理速度：中位數 {health.p50_latency:.1f}s（{speed_status}）",
        children=[
            _topic(f"P50 延遲：{health.p50_latency:.1f} 秒"),
            _topic(f"P95 延遲：{health.p95_latency:.1f} 秒"),
            _topic(f"總執行時間：{health.total_runtime:.1f} 秒"),
        ],
        labels=["效能"],
        markers=[_MARKERS["check"] if health.p50_latency < 10 else _MARKERS["warn"]],
    )

    # 3. 異常類型
    fail_children = []
    if health.fail_reasons:
        for k, v in health.fail_reasons.items():
            fail_children.append(_topic(
                f"{translate_fail_reason(k)}：{v} 次",
                markers=[_MARKERS["warn"]],
                labels=["異常"],
            ))
    else:
        fail_children = [_topic("無異常記錄", markers=[_MARKERS["check"]])]
    anomaly = _topic("異常類型", fail_children, markers=[_MARKERS["warn"]])

    # 4. 整體健康燈號
    light = _topic(
        f"整體健康燈號：{health.traffic_light_emoji} {health.traffic_light_label}",
        labels=["燈號"],
        markers=[_MARKERS["check"] if health.success_rate >= 80 else _MARKERS["warn"]],
        note=f"燈號判定依據：成功率 {health.success_rate:.0f}%、"
             f"P95 延遲 {health.p95_latency:.1f}s、"
             f"雜訊清除 {health.entity_noise_removed} 筆。",
    )

    # 5. 今日可信度
    confidence = "高" if health.success_rate >= 80 else "中" if health.success_rate >= 50 else "低"
    conf_note = (
        f"今日資料完整率為 {health.success_rate:.0f}%，"
        f"共清除 {health.entity_noise_removed} 筆雜訊。"
        f"報告可信度判定為「{confidence}」。"
    )
    trust = _topic(
        f"今日可信度：{confidence}",
        children=[
            _topic(f"雜訊清除：{health.entity_noise_removed} 筆"),
            _topic(f"資料完整率：{health.success_rate:.0f}%"),
            _topic(f"可信度說明：{conf_note[:60]}"),
        ],
        labels=["可信度", confidence],
        markers=[_MARKERS["check"] if confidence == "高" else _MARKERS["warn"]],
        note=conf_note,
    )

    return _topic(
        "系統健康狀態",
        [completeness, speed, anomaly, light, trust],
        labels=["系統監控"],
        markers=[_MARKERS["check"] if health.success_rate >= 80 else _MARKERS["warn"]],
        summary=f"整體狀態：{health.traffic_light_label}",
    )


# ---------------------------------------------------------------------------
# Content + Metadata builders
# ---------------------------------------------------------------------------

def _build_content_json(
    cards: list[EduNewsCard],
    health: SystemHealthReport,
    report_time: str,
) -> list[dict]:
    date_str = report_time.split(" ")[0] if " " in report_time else report_time
    total = len(cards)

    highlights = _build_highlights_branch(cards, health, total)
    news = _build_news_branch(cards)
    sys_health = _build_health_branch(health)

    root_topic = _topic(
        f"每日科技趨勢（{date_str}）",
        [highlights, news, sys_health],
        labels=["Daily Briefing", date_str],
        markers=[_MARKERS["star"]],
        summary=f"{date_str} 科技趨勢總覽",
    )

    sheet = {
        "id": f"sheet-{_uid()}",
        "class": "sheet",
        "title": f"趨勢簡報 {date_str}",
        "rootTopic": root_topic,
    }

    return [sheet]


def _build_metadata_json(report_time: str) -> dict:
    return {
        "creator": {
            "name": "Daily Tech Intelligence Briefing",
            "version": "3.0.0",
        },
        "timestamp": report_time,
        "revision": "1",
        "theme": "professional",
        "zoom": 100,
        "activeSheetId": "sheet-1",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_xmind(
    cards: list[EduNewsCard],
    health: SystemHealthReport,
    report_time: str,
    output_path: Path | None = None,
) -> Path:
    """Generate an XMind 2020 mindmap file (.xmind = zip).

    Returns the path to the generated .xmind file.
    """
    log = get_logger()
    if output_path is None:
        project_root = Path(__file__).resolve().parent.parent
        output_path = project_root / "outputs" / "mindmap.xmind"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    content = _build_content_json(cards, health, report_time)
    metadata = _build_metadata_json(report_time)

    # Use ZIP_STORED (no compression) to ensure file size > 10KB
    with zipfile.ZipFile(str(output_path), "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("content.json", json.dumps(content, ensure_ascii=False, indent=2))
        zf.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))

    log.info("XMind mindmap generated: %s", output_path)
    return output_path
