"""PPTX 總經理版簡報生成器 — Notion 文檔式排版。

白底、左對齊、大留白、細分隔線、無厚重色塊。
每則新聞一頁一概念；圖片上方全寬 banner + 下方文字。
色彩系統：#212838 深藍文字 + #E65A37 橘色 accent。
含嵌入圖片、6 欄決策卡、名詞白話解釋、決策摘要表格。

禁用詞彙：ai捕捉、AI Intel、Z1~Z5、pipeline、ETL、verify_run、ingestion、ai_core
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Cm, Pt

from core.content_strategy import (
    build_ceo_article_blocks,
    build_decision_card,
    build_term_explainer,
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
DARK_TEXT = RGBColor(33, 40, 56)       # #212838 — primary text
ACCENT = RGBColor(230, 90, 55)        # #E65A37 — orange accent
WHITE = RGBColor(255, 255, 255)
BG_WHITE = RGBColor(255, 255, 255)    # slide background
LIGHT_GRAY = RGBColor(180, 180, 180)  # subtle text
MID_GRAY = RGBColor(120, 120, 120)    # secondary text
TABLE_HEADER_BG = RGBColor(245, 245, 245)  # very light gray for table headers

SLIDE_WIDTH = Cm(33.867)   # 16:9 default
SLIDE_HEIGHT = Cm(19.05)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def safe_text(text: str, limit: int = 120) -> str:
    """Truncate text to limit, adding ellipsis if needed."""
    t = sanitize(text)
    return t if len(t) <= limit else t[:limit] + "…"


def _set_slide_bg(slide, color: RGBColor = BG_WHITE) -> None:
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = color


def _add_textbox(
    slide, left, top, width, height, text: str,
    font_size: int = 18, color: RGBColor = DARK_TEXT,
    bold: bool = False, alignment: PP_ALIGN = PP_ALIGN.LEFT,
) -> None:
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    p = tf.paragraphs[0]
    p.text = safe_text(text)
    p.font.size = Pt(font_size)
    p.font.color.rgb = color
    p.font.bold = bold
    p.alignment = alignment


def _add_multiline_textbox(
    slide, left, top, width, height, lines: list[str],
    font_size: int = 14, color: RGBColor = DARK_TEXT,
    bold_first: bool = False, line_spacing: float = 1.5,
) -> None:
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = safe_text(line)
        p.font.size = Pt(font_size)
        p.font.color.rgb = color
        if bold_first and i == 0:
            p.font.bold = True
        p.space_after = Pt(font_size * (line_spacing - 1))


def _add_divider(slide, left, top, width, color: RGBColor = ACCENT) -> None:
    line = slide.shapes.add_shape(1, left, top, width, Cm(0.05))
    line.fill.solid()
    line.fill.fore_color.rgb = color
    line.line.fill.background()


def _add_table_slide(prs: Presentation, title: str,
                     headers: list[str], rows: list[list[str]]) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)
    _add_textbox(slide, Cm(2), Cm(1.2), Cm(30), Cm(2),
                 title, font_size=28, bold=True, color=DARK_TEXT)
    _add_divider(slide, Cm(2), Cm(3.2), Cm(4), color=ACCENT)

    n_rows = len(rows) + 1
    n_cols = len(headers)
    table_shape = slide.shapes.add_table(
        n_rows, n_cols,
        Cm(1.5), Cm(4), Cm(31), Cm(min(n_rows * 1.5, 14)),
    )
    tbl = table_shape.table
    for ci, h in enumerate(headers):
        cell = tbl.cell(0, ci)
        cell.text = safe_text(h)
        for p in cell.text_frame.paragraphs:
            p.font.size = Pt(10)
            p.font.bold = True
            p.font.color.rgb = DARK_TEXT
        cell.fill.solid()
        cell.fill.fore_color.rgb = TABLE_HEADER_BG
    for ri, row_data in enumerate(rows):
        for ci, val in enumerate(row_data):
            cell = tbl.cell(ri + 1, ci)
            cell.text = safe_text(val)
            for p in cell.text_frame.paragraphs:
                p.font.size = Pt(9)
                p.font.color.rgb = DARK_TEXT
            cell.fill.solid()
            cell.fill.fore_color.rgb = WHITE


# ---------------------------------------------------------------------------
# Slide builders
# ---------------------------------------------------------------------------


def _slide_cover(prs: Presentation, report_time: str) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)
    _add_divider(slide, Cm(0), Cm(0.5), SLIDE_WIDTH, color=ACCENT)
    _add_textbox(slide, Cm(4), Cm(5.5), Cm(26), Cm(4),
                 "每日科技趨勢簡報", font_size=44, bold=True,
                 color=DARK_TEXT, alignment=PP_ALIGN.CENTER)
    _add_textbox(slide, Cm(4), Cm(10), Cm(26), Cm(2),
                 "Daily Tech Intelligence Briefing", font_size=20,
                 color=MID_GRAY, alignment=PP_ALIGN.CENTER)
    _add_divider(slide, Cm(15.5), Cm(12.5), Cm(3), color=ACCENT)
    _add_textbox(slide, Cm(4), Cm(14), Cm(26), Cm(1.5),
                 report_time, font_size=14, color=LIGHT_GRAY,
                 alignment=PP_ALIGN.CENTER)


def _slide_key_takeaways(prs: Presentation, cards: list[EduNewsCard],
                         health: SystemHealthReport, total_items: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)
    _add_textbox(slide, Cm(2), Cm(1.2), Cm(30), Cm(2),
                 "Key Takeaways", font_size=36, bold=True, color=DARK_TEXT)
    _add_divider(slide, Cm(2), Cm(3.2), Cm(4), color=ACCENT)

    valid_cards = [c for c in cards if c.is_valid_news]
    takeaways: list[str] = []
    takeaways.append(f"本日分析 {total_items} 則，{len(valid_cards)} 則值得關注")
    for c in valid_cards[:3]:
        dc = build_decision_card(c)
        takeaways.append(f"{safe_text(c.title_plain, 30)} — {dc['event']}")
    takeaways.append(f"資料完整率 {health.success_rate:.0f}%｜{health.traffic_light_label}")

    _add_multiline_textbox(
        slide, Cm(3), Cm(4.5), Cm(28), Cm(13),
        takeaways, font_size=18, color=DARK_TEXT, line_spacing=1.8,
    )


def _slide_overview_table(prs: Presentation, cards: list[EduNewsCard]) -> None:
    valid_cards = [c for c in cards if c.is_valid_news]
    if not valid_cards:
        return
    headers = ["#", "標題", "類別", "評分"]
    rows = []
    for i, c in enumerate(valid_cards[:8], 1):
        rows.append([
            str(i), safe_text(c.title_plain, 30),
            c.category or "綜合", f"{c.final_score:.1f}",
        ])
    _add_table_slide(prs, "今日總覽  Overview", headers, rows)


def _slide_section(prs: Presentation, title: str, subtitle: str = "") -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)
    _add_divider(slide, Cm(2), Cm(6), Cm(4), color=ACCENT)
    _add_textbox(slide, Cm(3), Cm(6.5), Cm(28), Cm(4),
                 title, font_size=36, bold=True, color=DARK_TEXT)
    if subtitle:
        _add_textbox(slide, Cm(3), Cm(10.5), Cm(28), Cm(2),
                     subtitle, font_size=16, color=MID_GRAY)


def _slide_text(prs: Presentation, title: str, body_lines: list[str]) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)
    _add_textbox(slide, Cm(2), Cm(1.2), Cm(30), Cm(2),
                 title, font_size=28, bold=True, color=DARK_TEXT)
    _add_divider(slide, Cm(2), Cm(3.2), Cm(4), color=ACCENT)
    _add_multiline_textbox(slide, Cm(2.5), Cm(4), Cm(29), Cm(14),
                           body_lines, font_size=15, color=DARK_TEXT)


def _slide_image_text(prs: Presentation, title: str,
                      body_lines: list[str], img_path: Path | None) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)
    _add_textbox(slide, Cm(2), Cm(0.6), Cm(30), Cm(1.5),
                 title, font_size=22, bold=True, color=DARK_TEXT)
    if img_path and img_path.exists():
        try:
            slide.shapes.add_picture(
                str(img_path), Cm(1), Cm(2.2), Cm(31.8), Cm(7.5),
            )
        except Exception:
            pass
    _add_multiline_textbox(slide, Cm(2), Cm(10.2), Cm(30), Cm(8.5),
                           body_lines, font_size=13, color=DARK_TEXT,
                           line_spacing=1.4)


def _slide_pending_decisions(prs: Presentation, cards: list[EduNewsCard]) -> None:
    """Last slide: pending decisions & owners."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)
    _add_textbox(slide, Cm(2), Cm(1.5), Cm(30), Cm(2.5),
                 "待決事項與 Owner", font_size=36, bold=True, color=DARK_TEXT)
    _add_divider(slide, Cm(2), Cm(3.8), Cm(4), color=ACCENT)

    items: list[str] = []
    valid_cards = [c for c in cards if c.is_valid_news]
    for i, c in enumerate(valid_cards[:5], 1):
        dc = build_decision_card(c)
        action = dc["actions"][0] if dc["actions"] else "待確認"
        owner = dc["owner"]
        items.append(f"{i}. {safe_text(action, 80)} → Owner: {owner}")

    if not items:
        items.append("1. 本日無待決事項")

    _add_multiline_textbox(slide, Cm(3), Cm(5), Cm(28), Cm(13),
                           items, font_size=18, color=DARK_TEXT,
                           line_spacing=1.8)
    _add_divider(slide, Cm(0), Cm(18.7), SLIDE_WIDTH, color=ACCENT)


# ---------------------------------------------------------------------------
# News card slides — two-page article layout per card
# Page 1: headline + hero image + one-liner + why-it-matters + what-to-do + quote
# Page 2: key terms with context-aware explanations + sources
# ---------------------------------------------------------------------------


def _slide_article_page1(prs: Presentation, card: EduNewsCard,
                         idx: int, article: dict) -> None:
    """Article page 1: headline, image, one-liner, why, what-to-do, quote."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    # Headline
    _add_textbox(slide, Cm(2), Cm(0.5), Cm(30), Cm(1.8),
                 f"#{idx}  {safe_text(article['headline_cn'], 35)}",
                 font_size=24, bold=True, color=DARK_TEXT)
    _add_divider(slide, Cm(2), Cm(2.3), Cm(4), color=ACCENT)

    # Hero image
    img_top = Cm(2.6)
    text_top = Cm(2.6)
    try:
        img_path = get_news_image(card.title_plain, card.category)
        if img_path and img_path.exists():
            slide.shapes.add_picture(
                str(img_path), Cm(1), img_top, Cm(31.8), Cm(6.5),
            )
            text_top = Cm(9.5)
    except Exception:
        pass

    # Article body
    body: list[str] = []
    body.append(safe_text(article["one_liner"], 200))
    body.append("")
    body.append(f"為什麼重要：{safe_text(article['why_it_matters'], 200)}")
    body.append("")
    body.append(f"下一步：{safe_text(article['what_to_do'], 150)}")
    if article["quote"]:
        body.append("")
        body.append(f"▌ 「{safe_text(article['quote'], 150)}」")

    _add_multiline_textbox(slide, Cm(2), text_top, Cm(30), Cm(18 - text_top.cm),
                           body, font_size=14, color=DARK_TEXT,
                           line_spacing=1.5)


def _slide_article_page2(prs: Presentation, card: EduNewsCard,
                         idx: int, article: dict) -> None:
    """Article page 2: key terms with context-aware explanations + sources."""
    term_items = build_term_explainer(card)
    sources = article.get("sources", [])

    # Skip page 2 if no terms and no sources
    if not term_items and not sources:
        return

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    _add_textbox(slide, Cm(2), Cm(0.8), Cm(30), Cm(1.5),
                 f"#{idx}  關鍵名詞解讀",
                 font_size=22, bold=True, color=DARK_TEXT)
    _add_divider(slide, Cm(2), Cm(2.3), Cm(4), color=ACCENT)

    lines: list[str] = []
    for item in term_items:
        lines.append(f"{item['term']}：{safe_text(item['explain'], 180)}")
        lines.append("")

    if sources:
        lines.append("——————")
        lines.append("原始來源：")
        for src in sources[:3]:
            lines.append(safe_text(src, 100))

    _add_multiline_textbox(slide, Cm(2.5), Cm(3), Cm(29), Cm(15),
                           lines, font_size=13, color=DARK_TEXT,
                           line_spacing=1.4)


def _slides_news_card(prs: Presentation, card: EduNewsCard, idx: int) -> None:
    if not card.is_valid_news:
        _slide_text(prs, f"#{idx} — 無效內容", [
            f"判定：{card.invalid_reason or '非新聞內容'}",
            "", f"原因：{card.invalid_cause or '資料抓取異常'}",
            f"處理建議：{card.invalid_fix or '調整來源設定'}",
        ])
        return

    article = build_ceo_article_blocks(card)
    _slide_article_page1(prs, card, idx, article)
    _slide_article_page2(prs, card, idx, article)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_executive_ppt(
    cards: list[EduNewsCard],
    health: SystemHealthReport,
    report_time: str,
    total_items: int,
    output_path: Path | None = None,
) -> Path:
    log = get_logger()
    if output_path is None:
        project_root = Path(__file__).resolve().parent.parent
        output_path = project_root / "outputs" / "executive_report.pptx"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    prs = Presentation()
    prs.slide_width = SLIDE_WIDTH
    prs.slide_height = SLIDE_HEIGHT

    _slide_cover(prs, report_time)
    _slide_key_takeaways(prs, cards, health, total_items)
    _slide_overview_table(prs, cards)

    valid_cards = [c for c in cards if c.is_valid_news]
    invalid_cards = [c for c in cards if not c.is_valid_news]

    if valid_cards:
        _slide_section(prs, "新聞深度解析", "News Analysis")
        for i, card in enumerate(valid_cards, 1):
            _slides_news_card(prs, card, i)

    # Decision Summary Table (6 columns)
    if valid_cards:
        decision_rows = []
        for i, c in enumerate(valid_cards[:8], 1):
            dc = build_decision_card(c)
            decision_rows.append([
                str(i),
                safe_text(dc["event"], 18),
                safe_text(dc["effects"][0], 20) if dc["effects"] else "缺口",
                safe_text(dc["risks"][0], 20) if dc["risks"] else "缺口",
                safe_text(dc["actions"][0], 25) if dc["actions"] else "待確認",
                dc["owner"],
            ])
        _add_table_slide(
            prs, "決策摘要表  Decision Matrix",
            ["#", "事件", "影響", "風險", "建議行動", "要問誰"],
            decision_rows,
        )

    for i, card in enumerate(invalid_cards, len(valid_cards) + 1):
        _slides_news_card(prs, card, i)

    # No system health / metrics slide — removed per CEO deck requirements

    _slide_pending_decisions(prs, cards)

    prs.save(str(output_path))
    log.info("Executive PPTX generated: %s", output_path)
    return output_path
