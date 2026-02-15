"""Notion 總經理版頁面生成器。

輸出 outputs/notion_page.md，可直接貼到 Notion 的一頁模板。
包含：今日重點、每則新聞決策卡（表格/checkbox）、風險清單、待決問題。

禁用詞彙：ai捕捉、AI Intel、Z1~Z5、pipeline、ETL、verify_run、ingestion、ai_core
"""

from __future__ import annotations

import re
from pathlib import Path

from schemas.education_models import EduNewsCard, SystemHealthReport
from utils.logger import get_logger

_BANNED_OUTPUT_TERMS = (
    "AI Intel",
    "Z1",
    "Z2",
    "Z3",
    "Z4",
    "Z5",
    "pipeline",
    "ETL",
    "verify_run",
    "ingestion",
    "ai_core",
)


def _strip_banned_terms(text: str) -> str:
    cleaned = text
    for term in _BANNED_OUTPUT_TERMS:
        cleaned = re.sub(re.escape(term), "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned


def generate_notion_page(
    cards: list[EduNewsCard],
    health: SystemHealthReport,
    report_time: str,
    total_items: int,
    output_path: Path | None = None,
) -> Path:
    """Generate a Notion-ready markdown page.

    Returns the path to the generated notion_page.md.
    """
    log = get_logger()
    if output_path is None:
        project_root = Path(__file__).resolve().parent.parent
        output_path = project_root / "outputs" / "notion_page.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    valid_cards = [c for c in cards if c.is_valid_news]
    valid_count = len(valid_cards)
    invalid_count = len(cards) - valid_count

    lines: list[str] = []

    # ===== Header =====
    lines.extend([
        "# 每日科技趨勢簡報（總經理版）",
        "",
        f"> 報告時間：{report_time}　｜　分析項目：{total_items} 則"
        f"　｜　資料完整率：{health.success_rate:.0f}%"
        f"　｜　{health.traffic_light_emoji} {health.traffic_light_label}",
        "",
        "---",
        "",
    ])

    # ===== 今日重點 =====
    lines.extend([
        "## 今日重點",
        "",
        f"本次分析共處理 **{total_items}** 則資料項目，"
        f"其中 **{valid_count}** 則為有效新聞"
        + (f"、**{invalid_count}** 則為無效內容" if invalid_count else "")
        + "。",
        "",
    ])

    if valid_cards:
        lines.append("| # | 標題 | 類別 | 評分 | 關注重點 |")
        lines.append("|---|------|------|------|----------|")
        for i, c in enumerate(valid_cards, 1):
            focus = c.focus_action[:40] if c.focus_action else "—"
            lines.append(
                f"| {i} | {c.title_plain[:35]} | {c.category or '綜合'} "
                f"| {c.final_score:.1f} | {focus} |"
            )
        lines.append("")

    lines.extend(["---", ""])

    # ===== 決策卡 =====
    lines.extend([
        "## 決策卡",
        "",
    ])

    for i, card in enumerate(cards, 1):
        if not card.is_valid_news:
            lines.extend([
                f"### 第 {i} 則：無效內容",
                "",
                f"- 原因：{card.invalid_cause or '資料抓取異常'}",
                f"- 處理建議：{card.invalid_fix or '調整來源設定'}",
                "",
            ])
            continue

        lines.extend([
            f"### 第 {i} 則：{card.title_plain[:50]}",
            "",
            f"**事件概要：** {card.what_happened[:150]}",
            "",
            f"**為何重要：** {card.why_important[:150]}",
            "",
            f"**關注重點：** {card.focus_action[:150]}",
            "",
        ])

        # Decision table
        lines.append("| 影響面向 | 風險程度 | 建議行動 |")
        lines.append("|----------|----------|----------|")
        effects = card.derivable_effects[:3] if card.derivable_effects else ["待評估"]
        risks = card.speculative_effects[:3] if card.speculative_effects else ["低"]
        actions = card.action_items[:3] if card.action_items else ["持續觀察"]
        for j in range(max(len(effects), 1)):
            eff = effects[j][:40] if j < len(effects) else "—"
            risk = risks[j][:40] if j < len(risks) else "待觀察"
            act = actions[j][:50] if j < len(actions) else "—"
            lines.append(f"| {eff} | {risk} | {act} |")
        lines.append("")

        # Checkbox actions
        lines.append("**追蹤清單：**")
        lines.append("")
        for act in card.action_items[:3]:
            lines.append(f"- [ ] {act[:80]}")
        if not card.action_items:
            lines.append("- [ ] 持續觀察後續發展")
        lines.append("")

        # Source
        if card.source_url and card.source_url.startswith("http"):
            lines.append(f"🔗 [原始來源]({card.source_url})")
            lines.append("")

    lines.extend(["---", ""])

    # ===== 風險清單 =====
    lines.extend([
        "## 風險清單",
        "",
    ])

    risk_items = []
    for c in valid_cards:
        for risk in c.speculative_effects[:2]:
            risk_items.append(f"- {c.title_plain[:20]}：{risk[:60]}")

    if risk_items:
        lines.extend(risk_items[:10])
    else:
        lines.append("- 本次無明顯高風險項目")
    lines.extend(["", "---", ""])

    # ===== 待決問題 =====
    lines.extend([
        "## 待決問題",
        "",
        "- [ ] 檢視今日新聞中與自身業務相關的事件，評估需否列入決策議程",
        "- [ ] 針對高風險事件指派專人追蹤後續發展",
        "- [ ] 回顧過去一週趨勢，辨識重複出現的主題模式",
        "- [ ] 確認系統運作狀態是否正常",
        "",
    ])

    # ===== 系統狀態摘要 =====
    lines.extend([
        "---",
        "",
        f"> 系統狀態：{health.traffic_light_emoji} {health.traffic_light_label}"
        f"　｜　資料完整率 {health.success_rate:.0f}%"
        f"　｜　處理時間 {health.total_runtime:.1f}s",
        "",
        "*本報告由自動化趨勢分析系統生成*",
        "",
    ])

    content = _strip_banned_terms("\n".join(lines))
    output_path.write_text(content, encoding="utf-8")
    log.info("Notion page generated: %s", output_path)
    return output_path
