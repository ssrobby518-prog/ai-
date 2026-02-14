"""DOCX 總經理版科技趨勢簡報生成器 — Notion 風格極簡設計。

極簡、留白、大標題。黑白灰為主色，#212838 深藍 + #E65A37 橘色 accent。
Callout box（▌重點提示框）+ Divider 分隔線 + Notion 風格簡潔表格。
每則新聞：事件一句話/已知事實/為什麼重要/可能影響/建議下一步/關鍵引述/名詞解釋/來源。

禁用詞彙：ai捕捉、AI Intel、Z1~Z5、pipeline、ETL、verify_run、ingestion、ai_core
禁用系統運作字眼：系統健康、資料可信度、延遲、P95、雜訊清除、健康狀態
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from core.content_strategy import (
    build_ceo_article_blocks,
    build_decision_card,
    build_executive_qa,
    build_term_explainer,
    is_non_event_or_index,
    sanitize,
)
from core.image_helper import get_news_image
from schemas.education_models import (
    EduNewsCard,
    SystemHealthReport,
)
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
    heading = doc.add_heading(sanitize(text), level=level)
    for run in heading.runs:
        run.font.color.rgb = DARK_TEXT


def _add_divider(doc: Document) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(6)
    pPr = p._element.get_or_add_pPr()
    pBdr = pPr.makeelement(qn("w:pBdr"), {})
    bottom = pBdr.makeelement(qn("w:bottom"), {
        qn("w:val"): "single", qn("w:sz"): "4",
        qn("w:space"): "1", qn("w:color"): "E0E0E0",
    })
    pBdr.append(bottom)
    pPr.append(pBdr)


def _add_callout(doc: Document, title: str, lines: list[str]) -> None:
    p_title = doc.add_paragraph()
    p_title.paragraph_format.left_indent = Cm(0.8)
    p_title.paragraph_format.space_before = Pt(8)
    run_bar = p_title.add_run("▌ ")
    run_bar.font.color.rgb = ACCENT_COLOR
    run_bar.font.size = Pt(13)
    run_bar.bold = True
    run_title = p_title.add_run(sanitize(title))
    run_title.font.color.rgb = DARK_TEXT
    run_title.font.size = Pt(13)
    run_title.bold = True
    for line in lines:
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(1.2)
        p.paragraph_format.space_before = Pt(1)
        p.paragraph_format.space_after = Pt(1)
        run = p.add_run(sanitize(line))
        run.font.size = Pt(10.5)
        run.font.color.rgb = DARK_TEXT
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_before = Pt(2)
    spacer.paragraph_format.space_after = Pt(2)


def _add_bold_label(doc: Document, label: str, value: str) -> None:
    p = doc.add_paragraph()
    run_label = p.add_run(f"{sanitize(label)}：")
    run_label.bold = True
    run_label.font.size = Pt(11)
    run_label.font.color.rgb = DARK_TEXT
    run_value = p.add_run(sanitize(value))
    run_value.font.size = Pt(11)


def _add_bullet(doc: Document, text: str, bold: bool = False) -> None:
    p = doc.add_paragraph(sanitize(text), style="List Bullet")
    if bold:
        for run in p.runs:
            run.bold = True


def _make_simple_table(doc: Document, headers: list[str],
                       rows: list[list[str]]) -> None:
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = sanitize(h)
        for p in hdr[i].paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in p.runs:
                run.bold = True
                run.font.size = Pt(10)
                run.font.color.rgb = DARK_TEXT
    for row_data in rows:
        row = table.add_row().cells
        for i, val in enumerate(row_data):
            row[i].text = sanitize(val)
            for p in row[i].paragraphs:
                for run in p.runs:
                    run.font.size = Pt(10)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_cover_section(doc: Document, report_time: str, total_items: int,
                         health: SystemHealthReport) -> None:
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
    # No system health metrics — CEO deck only shows item count
    run_info = info_p.add_run(f"{report_time}  |  {total_items} 則分析")
    run_info.font.size = Pt(11)
    run_info.font.color.rgb = GRAY_COLOR
    doc.add_page_break()


def _build_key_takeaways(doc: Document, cards: list[EduNewsCard],
                         total_items: int) -> None:
    """Key takeaways — NO system health/metrics."""
    event_cards = [c for c in cards if c.is_valid_news and not is_non_event_or_index(c)]
    lines = [f"本日分析 {total_items} 則，{len(event_cards)} 則值得關注。"]
    for c in event_cards[:3]:
        dc = build_decision_card(c)
        lines.append(f"• {sanitize(c.title_plain[:40])} — {dc['event']}")
    if not event_cards:
        lines.append("本日無重大事件需要決策。")
    _add_callout(doc, "Key Takeaways", lines)
    _add_divider(doc)


def _build_overview_table(doc: Document, cards: list[EduNewsCard]) -> None:
    _add_heading(doc, "今日總覽", level=1)
    event_cards = [c for c in cards if c.is_valid_news and not is_non_event_or_index(c)]
    non_event = [c for c in cards if c.is_valid_news and is_non_event_or_index(c)]
    invalid_count = len(cards) - len(event_cards) - len(non_event)
    p = doc.add_paragraph(
        f"共 {len(cards)} 則資料，{len(event_cards)} 則事件新聞"
        + (f"、{len(non_event)} 則索引/非事件已排除" if non_event else "")
        + (f"、{invalid_count} 則無效已過濾" if invalid_count else "") + "。"
    )
    p.runs[0].font.size = Pt(11)
    if event_cards:
        rows = []
        for i, card in enumerate(event_cards, 1):
            rows.append([
                str(i), sanitize(card.title_plain[:35]),
                card.category or "綜合", f"{card.final_score:.1f}",
            ])
        _make_simple_table(doc, ["#", "標題", "類別", "評分"], rows)
    doc.add_paragraph("")


def _build_news_card_section(doc: Document, card: EduNewsCard, idx: int) -> None:
    _add_divider(doc)
    _add_heading(doc, f"第 {idx} 則：{sanitize(card.title_plain[:45])}", level=2)

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
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    except Exception:
        pass

    # Article-style content blocks (same structure as PPT)
    article = build_ceo_article_blocks(card)

    # 1. Event one-liner
    _add_bold_label(doc, "事件", article["one_liner"])

    # 2. Known facts
    facts = article.get("known_facts", [])
    if facts:
        _add_callout(doc, "已知事實", [f"• {sanitize(f)}" for f in facts[:3]])

    # 3. Why it matters
    why_parts = article.get("why_it_matters", [])
    if why_parts:
        _add_callout(doc, "為什麼重要", [f"• {sanitize(w)}" for w in why_parts[:3]])

    # 4. Possible impact
    impacts = article.get("possible_impact", [])
    if impacts:
        _add_callout(doc, "可能影響", [f"• {sanitize(imp)}" for imp in impacts[:3]])

    # 5. What to do
    actions = article.get("what_to_do", [])
    if actions:
        _add_callout(doc, "建議下一步", [f"• {sanitize(a)}" for a in actions[:2]])

    # 6. Quote
    if article.get("quote"):
        _add_callout(doc, "關鍵引述", [article["quote"]])

    # 7. Key terms — CEO-readable explanations
    term_items = build_term_explainer(card)
    if term_items:
        term_lines = [f"{it['term']}：{sanitize(it['explain'])}" for it in term_items]
        _add_callout(doc, "重要名詞白話解釋", term_lines)

    # 8. Source
    if card.source_url and card.source_url.startswith("http"):
        p_src = doc.add_paragraph()
        run_src = p_src.add_run(f"原始來源：{card.source_url}")
        run_src.font.size = Pt(9)
        run_src.font.color.rgb = GRAY_COLOR

    # Decision table (compact reference)
    dc = build_decision_card(card)
    table_rows = []
    max_r = max(len(dc["effects"]), len(dc["risks"]), len(dc["actions"]), 1)
    for i in range(max_r):
        table_rows.append([
            dc["effects"][i][:40] if i < len(dc["effects"]) else "—",
            dc["risks"][i][:40] if i < len(dc["risks"]) else "—",
            dc["actions"][i][:50] if i < len(dc["actions"]) else "—",
            dc["owner"] if i == 0 else "",
        ])
    _make_simple_table(doc, ["影響面向", "風險程度", "建議行動", "要問誰"], table_rows)

    # 總經理決策 QA
    qa_lines = build_executive_qa(card, dc)
    _add_callout(doc, "總經理決策 QA", qa_lines)


def _build_conclusion_section(doc: Document, cards: list[EduNewsCard]) -> None:
    doc.add_page_break()
    _add_heading(doc, "待決事項與 Owner", level=1)

    items: list[str] = []
    event_cards = [c for c in cards if c.is_valid_news and not is_non_event_or_index(c)]
    for i, c in enumerate(event_cards[:5], 1):
        dc = build_decision_card(c)
        action = dc["actions"][0] if dc["actions"] else "待確認"
        items.append(f"{i}. {action} → Owner: {dc['owner']}")

    if not items:
        items.append("1. 本日無待決事項")

    _add_callout(doc, "待決事項", items)
    _add_divider(doc)

    footer = doc.add_paragraph()
    run_ft = footer.add_run("本報告由自動化趨勢分析產生")
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
    log = get_logger()
    if output_path is None:
        project_root = Path(__file__).resolve().parent.parent
        output_path = project_root / "outputs" / "executive_report.docx"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    _build_cover_section(doc, report_time, total_items, health)
    _build_key_takeaways(doc, cards, total_items)
    _build_overview_table(doc, cards)

    # Only include event cards in detail sections
    event_cards = [c for c in cards if c.is_valid_news and not is_non_event_or_index(c)]
    for i, card in enumerate(event_cards, 1):
        _build_news_card_section(doc, card, i)

    _build_conclusion_section(doc, cards)

    doc.save(str(output_path))
    log.info("Executive DOCX generated: %s", output_path)
    return output_path
