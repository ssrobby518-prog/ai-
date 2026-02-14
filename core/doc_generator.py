"""DOCX 總經理版科技趨勢簡報生成器 — Notion 風格極簡設計。

極簡、留白、大標題。黑白灰為主色，#212838 深藍 + #E65A37 橘色 accent。
Callout box（▌重點提示框）+ Divider 分隔線 + Notion 風格簡潔表格。
每則新聞：圖片 + 摘要 + 為何重要 + 風險 + 建議行動 + QA callout。

禁用詞彙：ai捕捉、AI Intel、Z1~Z5、pipeline、ETL、verify_run、ingestion、ai_core
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from core.image_helper import get_news_image
from schemas.education_models import EduNewsCard, SystemHealthReport
from utils.logger import get_logger

# ---------------------------------------------------------------------------
# Notion-style colour palette
# ---------------------------------------------------------------------------
DARK_TEXT = RGBColor(33, 40, 56)       # #212838
ACCENT_COLOR = RGBColor(230, 90, 55)   # #E65A37
GRAY_COLOR = RGBColor(120, 120, 120)
LIGHT_GRAY = RGBColor(200, 200, 200)


# ---------------------------------------------------------------------------
# Notion-style helpers
# ---------------------------------------------------------------------------


def _add_heading(doc: Document, text: str, level: int = 1) -> None:
    heading = doc.add_heading(text, level=level)
    for run in heading.runs:
        run.font.color.rgb = DARK_TEXT


def _add_divider(doc: Document) -> None:
    """Add a thin horizontal divider (Notion-style separator)."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(6)
    # Use a border-bottom on the paragraph
    pPr = p._element.get_or_add_pPr()
    pBdr = pPr.makeelement(qn("w:pBdr"), {})
    bottom = pBdr.makeelement(qn("w:bottom"), {
        qn("w:val"): "single",
        qn("w:sz"): "4",
        qn("w:space"): "1",
        qn("w:color"): "E0E0E0",
    })
    pBdr.append(bottom)
    pPr.append(pBdr)


def _add_callout(doc: Document, title: str, lines: list[str]) -> None:
    """Add a Notion-style callout box (▌ left-border accent + indented text)."""
    # Callout title with ▌ prefix
    p_title = doc.add_paragraph()
    p_title.paragraph_format.left_indent = Cm(0.8)
    p_title.paragraph_format.space_before = Pt(8)
    run_bar = p_title.add_run("▌ ")
    run_bar.font.color.rgb = ACCENT_COLOR
    run_bar.font.size = Pt(13)
    run_bar.bold = True
    run_title = p_title.add_run(title)
    run_title.font.color.rgb = DARK_TEXT
    run_title.font.size = Pt(13)
    run_title.bold = True

    # Callout body lines
    for line in lines:
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(1.2)
        p.paragraph_format.space_before = Pt(1)
        p.paragraph_format.space_after = Pt(1)
        run = p.add_run(line)
        run.font.size = Pt(10.5)
        run.font.color.rgb = DARK_TEXT

    # Small gap after callout
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_before = Pt(2)
    spacer.paragraph_format.space_after = Pt(2)


def _add_bold_label(doc: Document, label: str, value: str) -> None:
    p = doc.add_paragraph()
    run_label = p.add_run(f"{label}：")
    run_label.bold = True
    run_label.font.size = Pt(11)
    run_label.font.color.rgb = DARK_TEXT
    run_value = p.add_run(value)
    run_value.font.size = Pt(11)


def _add_bullet(doc: Document, text: str, bold: bool = False) -> None:
    p = doc.add_paragraph(text, style="List Bullet")
    if bold:
        for run in p.runs:
            run.bold = True


def _safe_topic(title: str) -> str:
    return re.sub(r"[^\w\s\u4e00-\u9fff]", "", title)[:25].strip()


def _make_simple_table(doc: Document, headers: list[str],
                       rows: list[list[str]]) -> None:
    """Create a Notion-style minimal table (no heavy borders)."""
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    # Header row
    hdr = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = h
        for p in hdr[i].paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in p.runs:
                run.bold = True
                run.font.size = Pt(10)
                run.font.color.rgb = DARK_TEXT

    # Data rows
    for row_data in rows:
        row = table.add_row().cells
        for i, val in enumerate(row_data):
            row[i].text = val
            for p in row[i].paragraphs:
                for run in p.runs:
                    run.font.size = Pt(10)


# ---------------------------------------------------------------------------
# Section builders — Notion-style
# ---------------------------------------------------------------------------


def _build_cover_section(doc: Document, report_time: str, total_items: int,
                         health: SystemHealthReport) -> None:
    """Title page — minimal, centered."""
    title = doc.add_heading("每日科技趨勢簡報", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in title.runs:
        run.font.color.rgb = DARK_TEXT

    subtitle = doc.add_paragraph("Daily Tech Intelligence Briefing")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.runs[0].font.size = Pt(14)
    subtitle.runs[0].font.color.rgb = GRAY_COLOR

    _add_divider(doc)

    info_p = doc.add_paragraph()
    info_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run_info = info_p.add_run(f"{report_time}  |  {total_items} 則分析  |  {health.traffic_light_emoji} {health.traffic_light_label}")
    run_info.font.size = Pt(11)
    run_info.font.color.rgb = GRAY_COLOR

    doc.add_page_break()


def _build_key_takeaways(doc: Document, cards: list[EduNewsCard],
                         health: SystemHealthReport, total_items: int) -> None:
    """Key Takeaways callout — first thing after cover."""
    valid_cards = [c for c in cards if c.is_valid_news]
    valid_count = len(valid_cards)

    takeaway_lines = [
        f"本日分析 {total_items} 則科技情報，{valid_count} 則值得關注。",
    ]
    for c in valid_cards[:3]:
        takeaway_lines.append(f"• {c.title_plain[:40]} — {c.what_happened[:60]}")
    takeaway_lines.append(f"系統狀態：{health.traffic_light_label}（資料完整率 {health.success_rate:.0f}%）")

    _add_callout(doc, "Key Takeaways", takeaway_lines)
    _add_divider(doc)


def _build_overview_table(doc: Document, cards: list[EduNewsCard]) -> None:
    """Overview table — simple, Notion-style."""
    _add_heading(doc, "今日總覽", level=1)

    valid_cards = [c for c in cards if c.is_valid_news]
    invalid_count = len(cards) - len(valid_cards)

    p = doc.add_paragraph(
        f"共 {len(cards)} 則資料，{len(valid_cards)} 則有效新聞"
        + (f"、{invalid_count} 則已過濾" if invalid_count else "")
        + "。"
    )
    p.runs[0].font.size = Pt(11)

    if valid_cards:
        headers = ["#", "標題", "類別", "評分"]
        rows = []
        for i, card in enumerate(valid_cards, 1):
            rows.append([
                str(i),
                card.title_plain[:35],
                card.category or "綜合",
                f"{card.final_score:.1f}",
            ])
        _make_simple_table(doc, headers, rows)

    doc.add_paragraph("")


def _build_news_card_section(doc: Document, card: EduNewsCard, idx: int) -> None:
    """One news card — image + summary + why it matters + risks + action + QA callout."""
    _add_divider(doc)
    _add_heading(doc, f"第 {idx} 則：{card.title_plain[:45]}", level=2)

    if not card.is_valid_news:
        _add_callout(doc, "無效內容", [
            f"判定：{card.invalid_reason or '非新聞內容'}",
            f"原因：{card.invalid_cause or '資料抓取異常'}",
            f"處理建議：{card.invalid_fix or '調整來源設定'}",
        ])
        return

    # Embedded image
    try:
        img_path = get_news_image(card.title_plain, card.category)
        if img_path.exists():
            doc.add_picture(str(img_path), width=Cm(14))
            last_paragraph = doc.paragraphs[-1]
            last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    except Exception:
        pass

    # Summary
    _add_bold_label(doc, "事件概要", card.what_happened[:200])
    _add_bold_label(doc, "為何重要", card.why_important[:200])
    _add_bold_label(doc, "關注重點", card.focus_action[:200])

    if card.metaphor:
        p = doc.add_paragraph()
        run = p.add_run(f"類比：{card.metaphor[:150]}")
        run.font.italic = True
        run.font.size = Pt(10)
        run.font.color.rgb = GRAY_COLOR

    # Decision table (影響 × 風險 × 建議行動) — minimal
    effects = card.derivable_effects[:3] if card.derivable_effects else ["待評估"]
    risks = card.speculative_effects[:3] if card.speculative_effects else ["低"]
    actions = card.action_items[:3] if card.action_items else ["持續觀察"]

    table_rows = []
    for i in range(max(len(effects), 1)):
        table_rows.append([
            effects[i][:40] if i < len(effects) else "—",
            risks[i][:40] if i < len(risks) else "待觀察",
            actions[i][:50] if i < len(actions) else "—",
        ])
    _make_simple_table(doc, ["影響面向", "風險程度", "建議行動"], table_rows)

    # Facts
    if card.fact_check_confirmed:
        doc.add_paragraph("")
        p_facts_title = doc.add_paragraph()
        run_ft = p_facts_title.add_run("事實確認")
        run_ft.bold = True
        run_ft.font.size = Pt(11)
        run_ft.font.color.rgb = DARK_TEXT
        for fact in card.fact_check_confirmed[:4]:
            _add_bullet(doc, fact[:80])

    # QA callout (executive decision-focused)
    short_title = card.title_plain[:20]
    _add_callout(doc, "決策者 QA", [
        f"Q：這件事對我們的業務有什麼影響？",
        f"「{short_title}」的影響可從三個層面評估："
        "直接的市場或技術面衝擊、對我方產品的連鎖效應、"
        "競爭對手可能的回應策略。建議在下次決策會議中討論。",
    ])

    # Resources
    safe = _safe_topic(card.title_plain)
    query = safe.replace(" ", "+")
    p_res = doc.add_paragraph()
    run_res = p_res.add_run(
        f"延伸資源：https://www.youtube.com/results?search_query={query}+analysis"
    )
    run_res.font.size = Pt(9)
    run_res.font.color.rgb = GRAY_COLOR

    if card.source_url and card.source_url.startswith("http"):
        p_src = doc.add_paragraph()
        run_src = p_src.add_run(f"原始來源：{card.source_url}")
        run_src.font.size = Pt(9)
        run_src.font.color.rgb = GRAY_COLOR


def _build_metrics_section(doc: Document, health: SystemHealthReport) -> None:
    _add_divider(doc)
    _add_heading(doc, "系統運作概況", level=1)

    rows_data = [
        ["資料完整率", f"{health.success_rate:.0f}%",
         "良好" if health.success_rate >= 80 else "注意" if health.success_rate >= 50 else "異常"],
        ["中位數延遲", f"{health.p50_latency:.1f}s",
         "正常" if health.p50_latency < 10 else "偏慢"],
        ["高延遲指標", f"{health.p95_latency:.1f}s",
         "正常" if health.p95_latency < 20 else "偏慢"],
        ["雜訊清除", f"{health.entity_noise_removed} 筆", "—"],
        ["總執行時間", f"{health.total_runtime:.1f}s", "—"],
    ]
    _make_simple_table(doc, ["指標", "數值", "狀態"], rows_data)

    doc.add_paragraph("")
    p = doc.add_paragraph()
    run = p.add_run(f"{health.traffic_light_emoji} 整體評估：{health.traffic_light_label}")
    run.bold = True
    run.font.size = Pt(12)

    if health.fail_reasons:
        p_fail = doc.add_paragraph()
        run_fail_title = p_fail.add_run("主要異常類型：")
        run_fail_title.bold = True
        run_fail_title.font.size = Pt(11)
        for reason, count in health.fail_reasons.items():
            _add_bullet(doc, f"{reason}：{count} 次")


def _build_conclusion_section(doc: Document) -> None:
    doc.add_page_break()
    _add_heading(doc, "待決問題與後續追蹤", level=1)

    _add_callout(doc, "Next Steps", [
        "1. 檢視今日新聞中與自身業務相關的事件，評估需否列入決策議程",
        "2. 針對高風險事件指派專人追蹤後續發展",
        "3. 回顧過去一週趨勢，辨識重複出現的主題模式",
    ])

    _add_divider(doc)

    footer = doc.add_paragraph()
    run_ft = footer.add_run("本報告由自動化趨勢分析系統生成")
    run_ft.font.size = Pt(9)
    run_ft.font.color.rgb = RGBColor(180, 180, 180)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_executive_docx(
    cards: list[EduNewsCard],
    health: SystemHealthReport,
    report_time: str,
    total_items: int,
    output_path: Path | None = None,
) -> Path:
    """Generate a Notion-style executive DOCX report.

    Returns the path to the generated .docx file.
    """
    log = get_logger()
    if output_path is None:
        project_root = Path(__file__).resolve().parent.parent
        output_path = project_root / "outputs" / "executive_report.docx"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()

    # Set default font
    style = doc.styles["Normal"]
    font = style.font
    font.name = "Calibri"
    font.size = Pt(11)

    # --- Build sections ---
    _build_cover_section(doc, report_time, total_items, health)
    _build_key_takeaways(doc, cards, health, total_items)
    _build_overview_table(doc, cards)

    # News cards
    for i, card in enumerate(cards, 1):
        _build_news_card_section(doc, card, i)

    # System metrics
    doc.add_page_break()
    _build_metrics_section(doc, health)

    # Conclusion
    _build_conclusion_section(doc)

    doc.save(str(output_path))
    log.info("Executive DOCX generated: %s", output_path)
    return output_path
