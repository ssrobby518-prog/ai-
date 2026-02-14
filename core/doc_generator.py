"""DOCX 教育版圖文教學報告生成器。

每則新聞包含：標題、淺白解釋、QA 區塊、圖片建議、YouTube 連結、下一步學習。
使用 python-docx 產出含標題樣式、粗體小標、條列清單的專業文件。
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor
from schemas.education_models import EduNewsCard, SystemHealthReport
from utils.logger import get_logger

# ---------------------------------------------------------------------------
# Style helpers
# ---------------------------------------------------------------------------

ACCENT_COLOR = RGBColor(230, 90, 55)
PRIMARY_COLOR = RGBColor(33, 40, 56)


def _add_heading(doc: Document, text: str, level: int = 1) -> None:
    """Add a heading with primary colour."""
    heading = doc.add_heading(text, level=level)
    for run in heading.runs:
        run.font.color.rgb = PRIMARY_COLOR


def _add_bold_label(doc: Document, label: str, value: str) -> None:
    """Add a paragraph with bold label + normal value."""
    p = doc.add_paragraph()
    run_label = p.add_run(f"{label}：")
    run_label.bold = True
    run_label.font.size = Pt(11)
    run_label.font.color.rgb = PRIMARY_COLOR
    run_value = p.add_run(value)
    run_value.font.size = Pt(11)


def _add_bullet(doc: Document, text: str, bold: bool = False) -> None:
    """Add a bulleted list item."""
    p = doc.add_paragraph(text, style="List Bullet")
    if bold:
        for run in p.runs:
            run.bold = True


def _add_qa_block(doc: Document, question: str, answer: str) -> None:
    """Add a Q&A pair."""
    p_q = doc.add_paragraph()
    run_q = p_q.add_run(f"Q：{question}")
    run_q.bold = True
    run_q.font.size = Pt(11)
    run_q.font.color.rgb = ACCENT_COLOR

    p_a = doc.add_paragraph()
    run_a = p_a.add_run(f"A：{answer}")
    run_a.font.size = Pt(11)


def _safe_topic(title: str) -> str:
    """Extract safe search topic from title."""
    return re.sub(r"[^\w\s\u4e00-\u9fff]", "", title)[:25].strip()


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_cover_section(doc: Document, report_time: str, total_items: int,
                         health: SystemHealthReport) -> None:
    """Build cover / header section."""
    title = doc.add_heading("AI 情報教育報告", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in title.runs:
        run.font.color.rgb = PRIMARY_COLOR

    subtitle = doc.add_paragraph("Daily Tech Intelligence")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.runs[0].font.size = Pt(14)
    subtitle.runs[0].font.color.rgb = RGBColor(120, 120, 120)

    doc.add_paragraph("")

    _add_bold_label(doc, "報告時間", report_time)
    _add_bold_label(doc, "分析項目數", f"{total_items} 則")
    _add_bold_label(doc, "成功率", f"{health.success_rate:.0f}%")
    _add_bold_label(doc, "健康狀態", f"{health.traffic_light_emoji} {health.traffic_light_label}")

    doc.add_page_break()


def _build_summary_section(doc: Document, cards: list[EduNewsCard],
                           health: SystemHealthReport) -> None:
    """Build executive summary section."""
    _add_heading(doc, "今日結論  Executive Summary", level=1)

    valid_cards = [c for c in cards if c.is_valid_news]
    valid_count = len(valid_cards)
    invalid_count = len(cards) - valid_count

    doc.add_paragraph(
        f"本次分析共處理 {len(cards)} 則資料項目，"
        f"其中 {valid_count} 則為有效新聞"
        + (f"、{invalid_count} 則為無效內容" if invalid_count else "")
        + "。"
    )

    if valid_cards:
        doc.add_paragraph("主要新聞主題：")
        for c in valid_cards[:5]:
            _add_bullet(doc, c.title_plain[:60])

    doc.add_paragraph("")


def _build_news_card_section(doc: Document, card: EduNewsCard, idx: int) -> None:
    """Build one news card section with all required elements."""

    # 1. Title
    _add_heading(doc, f"第 {idx} 則：{card.title_plain[:50]}", level=2)

    if not card.is_valid_news:
        p = doc.add_paragraph()
        run = p.add_run("⚠️ 此項目為無效內容，並非真實新聞。")
        run.bold = True
        run.font.color.rgb = ACCENT_COLOR
        _add_bold_label(doc, "原因", card.invalid_cause or "抓取失敗")
        _add_bold_label(doc, "修復建議", card.invalid_fix or "調整抓取策略")
        doc.add_paragraph("")
        return

    # 2. Plain explanation
    _add_heading(doc, "淺白解釋", level=3)
    _add_bold_label(doc, "發生了什麼", card.what_happened[:200])
    _add_bold_label(doc, "為什麼重要", card.why_important[:200])
    _add_bold_label(doc, "你要關注什麼", card.focus_action[:200])

    if card.metaphor:
        p = doc.add_paragraph()
        run = p.add_run(f"💡 類比理解：{card.metaphor[:150]}")
        run.font.italic = True
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(100, 100, 100)

    # Facts
    if card.fact_check_confirmed:
        _add_heading(doc, "事實核對", level=3)
        for fact in card.fact_check_confirmed[:4]:
            _add_bullet(doc, f"✅ {fact[:80]}")

    # Actions
    if card.action_items:
        _add_heading(doc, "可執行行動", level=3)
        for action in card.action_items[:3]:
            _add_bullet(doc, action[:100])

    # 3. QA block
    _add_heading(doc, "小問答時間", level=3)
    short_title = card.title_plain[:20]
    _add_qa_block(
        doc,
        f"「{short_title}」這件事跟一般人有什麼關係？",
        f"你可以把它想成工廠裡某條生產線換了新機器——"
        f"短期內工人要重新學習操作，但長期來看產量會增加。"
        f"「{short_title}」也是類似的道理：現在看起來只是產業新聞，"
        f"但未來可能影響到你用的產品或服務的價格和品質。",
    )

    # 4. Image suggestion (text placeholder — actual download requires network)
    _add_heading(doc, "圖解理解", level=3)
    safe = _safe_topic(card.title_plain)
    img_p = doc.add_paragraph()
    run_img = img_p.add_run(
        f"🖼️ 建議搜尋圖片：「{safe} 示意圖」\n"
        f"來源：https://unsplash.com/s/photos/{safe.replace(' ', '-')}"
    )
    run_img.font.size = Pt(10)
    run_img.font.color.rgb = RGBColor(80, 80, 80)

    # 5. YouTube link
    _add_heading(doc, "延伸影片", level=3)
    query = safe.replace(" ", "+")
    vid_p = doc.add_paragraph()
    run_vid = vid_p.add_run(
        f"🎬 YouTube 搜尋：「{safe} 分析解讀」\n"
        f"https://www.youtube.com/results?search_query={query}+explained"
    )
    run_vid.font.size = Pt(10)
    run_vid.font.color.rgb = RGBColor(80, 80, 80)

    # 6. Next learning step
    _add_heading(doc, "下一步學習", level=3)
    _add_bullet(doc, f"本週內：搜尋「{short_title}」的最新報導，確認事件進展")
    _add_bullet(doc, "兩週內：評估此事件對自身工作或投資的潛在影響")
    _add_bullet(doc, f"延伸閱讀：Google 搜尋「{safe} 產業分析」")

    doc.add_paragraph("")


def _build_metrics_section(doc: Document, health: SystemHealthReport) -> None:
    """Build system health section."""
    _add_heading(doc, "系統健康指標", level=1)

    table = doc.add_table(rows=1, cols=3)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text = "指標"
    hdr[1].text = "數值"
    hdr[2].text = "狀態"

    rows_data = [
        ("成功率", f"{health.success_rate:.0f}%",
         "良好" if health.success_rate >= 80 else "注意" if health.success_rate >= 50 else "異常"),
        ("P50 延遲", f"{health.p50_latency:.1f}s",
         "正常" if health.p50_latency < 10 else "偏慢"),
        ("P95 延遲", f"{health.p95_latency:.1f}s",
         "正常" if health.p95_latency < 20 else "偏慢"),
        ("雜訊清除", f"{health.entity_noise_removed} 個", "—"),
        ("總執行時間", f"{health.total_runtime:.1f}s", "—"),
    ]
    for label, value, status in rows_data:
        row = table.add_row().cells
        row[0].text = label
        row[1].text = value
        row[2].text = status

    doc.add_paragraph("")
    p = doc.add_paragraph()
    run = p.add_run(f"{health.traffic_light_emoji} 總體評估：{health.traffic_light_label}")
    run.bold = True
    run.font.size = Pt(13)


def _build_next_steps_section(doc: Document) -> None:
    """Build final next steps section."""
    doc.add_page_break()
    _add_heading(doc, "下一步學習  Next Steps", level=1)

    steps = [
        "今天：挑一則最感興趣的新聞，用自己的話說給朋友聽",
        "本週：完成至少一張新聞卡片裡的行動建議",
        "本月：回顧過去幾期報告，找出重複出現的趨勢關鍵字",
    ]
    for i, step in enumerate(steps, 1):
        p = doc.add_paragraph()
        run_num = p.add_run(f"{i}. ")
        run_num.bold = True
        run_num.font.color.rgb = ACCENT_COLOR
        run_text = p.add_run(step)
        run_text.font.size = Pt(11)

    doc.add_paragraph("")
    p = doc.add_paragraph()
    run = p.add_run(
        "學習科技趨勢就像每天看天氣預報——不需要懂氣象學，"
        "但知道明天會不會下雨，能幫你決定要不要帶傘。"
    )
    run.font.italic = True
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(100, 100, 100)

    doc.add_paragraph("")
    footer = doc.add_paragraph()
    run_ft = footer.add_run("本報告由 AI Intel Education Renderer (Z5) 自動生成")
    run_ft.font.size = Pt(9)
    run_ft.font.color.rgb = RGBColor(150, 150, 150)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_education_docx(
    cards: list[EduNewsCard],
    health: SystemHealthReport,
    report_time: str,
    total_items: int,
    output_path: Path | None = None,
) -> Path:
    """Generate an education-style DOCX report.

    Returns the path to the generated .docx file.
    """
    log = get_logger()
    if output_path is None:
        project_root = Path(__file__).resolve().parent.parent
        output_path = project_root / "outputs" / "education_report.docx"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()

    # Set default font
    style = doc.styles["Normal"]
    font = style.font
    font.name = "Calibri"
    font.size = Pt(11)

    # --- Build sections ---
    _build_cover_section(doc, report_time, total_items, health)
    _build_summary_section(doc, cards, health)

    # News cards
    for i, card in enumerate(cards, 1):
        _build_news_card_section(doc, card, i)

    # System metrics
    doc.add_page_break()
    _build_metrics_section(doc, health)

    # Next steps
    _build_next_steps_section(doc)

    doc.save(str(output_path))
    log.info("Education DOCX generated: %s", output_path)
    return output_path
