"""PPTX 總經理版簡報生成器 — Notion 風格極簡白底設計。

白底 + 大標題 + 極少文字 + 每頁一個觀點。
色彩系統：黑白灰為主，#212838 深藍 + #E65A37 橘色作為 accent。
含嵌入圖片與決策摘要表格。

禁用詞彙：ai捕捉、AI Intel、Z1~Z5、pipeline、ETL、verify_run、ingestion、ai_core
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Cm, Emu, Pt

from core.image_helper import get_news_image
from schemas.education_models import EduNewsCard, SystemHealthReport
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
TABLE_BORDER = RGBColor(230, 230, 230)     # subtle border

SLIDE_WIDTH = Cm(33.867)   # 16:9 default
SLIDE_HEIGHT = Cm(19.05)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_slide_bg(slide, color: RGBColor = BG_WHITE) -> None:
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = color


def _add_textbox(
    slide,
    left,
    top,
    width,
    height,
    text: str,
    font_size: int = 18,
    color: RGBColor = DARK_TEXT,
    bold: bool = False,
    alignment: PP_ALIGN = PP_ALIGN.LEFT,
) -> None:
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(font_size)
    p.font.color.rgb = color
    p.font.bold = bold
    p.alignment = alignment


def _add_multiline_textbox(
    slide,
    left,
    top,
    width,
    height,
    lines: list[str],
    font_size: int = 14,
    color: RGBColor = DARK_TEXT,
    bold_first: bool = False,
    line_spacing: float = 1.5,
) -> None:
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = line
        p.font.size = Pt(font_size)
        p.font.color.rgb = color
        if bold_first and i == 0:
            p.font.bold = True
        p.space_after = Pt(font_size * (line_spacing - 1))


def _add_divider(slide, left, top, width, color: RGBColor = ACCENT) -> None:
    """Add a thin horizontal divider line."""
    line = slide.shapes.add_shape(1, left, top, width, Cm(0.08))
    line.fill.solid()
    line.fill.fore_color.rgb = color
    line.line.fill.background()


def _add_table_slide(prs: Presentation, title: str,
                     headers: list[str], rows: list[list[str]]) -> None:
    """Add a Notion-style minimal table slide."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    _add_textbox(slide, Cm(2), Cm(1.2), Cm(30), Cm(2),
                 title, font_size=28, bold=True, color=DARK_TEXT)

    _add_divider(slide, Cm(2), Cm(3.2), Cm(5))

    n_rows = len(rows) + 1
    n_cols = len(headers)
    table_shape = slide.shapes.add_table(
        n_rows, n_cols,
        Cm(2), Cm(4), Cm(30), Cm(min(n_rows * 1.5, 14)),
    )
    tbl = table_shape.table

    # Header row — light gray background, dark text
    for ci, h in enumerate(headers):
        cell = tbl.cell(0, ci)
        cell.text = h
        for p in cell.text_frame.paragraphs:
            p.font.size = Pt(11)
            p.font.bold = True
            p.font.color.rgb = DARK_TEXT
        cell.fill.solid()
        cell.fill.fore_color.rgb = TABLE_HEADER_BG

    # Data rows — white background, dark text
    for ri, row_data in enumerate(rows):
        for ci, val in enumerate(row_data):
            cell = tbl.cell(ri + 1, ci)
            cell.text = val
            for p in cell.text_frame.paragraphs:
                p.font.size = Pt(10)
                p.font.color.rgb = DARK_TEXT
            cell.fill.solid()
            cell.fill.fore_color.rgb = WHITE


# ---------------------------------------------------------------------------
# Slide builders — Notion-style clean white design
# ---------------------------------------------------------------------------


def _slide_cover(prs: Presentation, report_time: str) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    # Thin accent bar at very top
    bar = slide.shapes.add_shape(1, Cm(0), Cm(0), SLIDE_WIDTH, Cm(0.4))
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()

    _add_textbox(slide, Cm(4), Cm(5.5), Cm(26), Cm(4),
                 "每日科技趨勢簡報", font_size=44, bold=True,
                 color=DARK_TEXT, alignment=PP_ALIGN.CENTER)
    _add_textbox(slide, Cm(4), Cm(10), Cm(26), Cm(2),
                 "Daily Tech Intelligence Briefing", font_size=20,
                 color=MID_GRAY, alignment=PP_ALIGN.CENTER)

    _add_divider(slide, Cm(13), Cm(12.5), Cm(8), color=ACCENT)

    _add_textbox(slide, Cm(4), Cm(14), Cm(26), Cm(1.5),
                 report_time, font_size=14, color=LIGHT_GRAY,
                 alignment=PP_ALIGN.CENTER)


def _slide_key_takeaways(prs: Presentation, cards: list[EduNewsCard],
                         health: SystemHealthReport, total_items: int) -> None:
    """Key Takeaways slide — 3-5 bullet points, one idea each."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    _add_textbox(slide, Cm(2), Cm(1.2), Cm(30), Cm(2),
                 "Key Takeaways", font_size=36, bold=True, color=DARK_TEXT)

    _add_divider(slide, Cm(2), Cm(3.2), Cm(5))

    valid_cards = [c for c in cards if c.is_valid_news]
    valid_count = len(valid_cards)

    takeaways: list[str] = []
    takeaways.append(f"本日分析 {total_items} 則科技情報，{valid_count} 則值得關注")

    for c in valid_cards[:3]:
        takeaways.append(f"{c.title_plain[:35]} — {c.what_happened[:50]}")

    status = health.traffic_light_label
    takeaways.append(f"系統運作狀態：{status}，資料完整率 {health.success_rate:.0f}%")

    _add_multiline_textbox(
        slide, Cm(3), Cm(4.5), Cm(28), Cm(13),
        takeaways, font_size=18, color=DARK_TEXT, line_spacing=1.8,
    )


def _slide_overview_table(prs: Presentation, cards: list[EduNewsCard]) -> None:
    """Overview table — simple list of today's news."""
    valid_cards = [c for c in cards if c.is_valid_news]
    if not valid_cards:
        return

    headers = ["#", "標題", "類別", "評分"]
    rows = []
    for i, c in enumerate(valid_cards[:8], 1):
        rows.append([
            str(i),
            c.title_plain[:30],
            c.category or "綜合",
            f"{c.final_score:.1f}",
        ])
    _add_table_slide(prs, "今日總覽  Overview", headers, rows)


def _slide_section(prs: Presentation, title: str, subtitle: str = "") -> None:
    """Section divider — large title, minimal."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    # Left accent bar
    bar = slide.shapes.add_shape(1, Cm(0), Cm(0), Cm(0.6), SLIDE_HEIGHT)
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()

    _add_textbox(slide, Cm(3), Cm(6.5), Cm(28), Cm(4),
                 title, font_size=36, bold=True, color=DARK_TEXT)
    if subtitle:
        _add_textbox(slide, Cm(3), Cm(10.5), Cm(28), Cm(2),
                     subtitle, font_size=16, color=MID_GRAY)


def _slide_text(prs: Presentation, title: str, body_lines: list[str]) -> None:
    """Text-only slide — clean, minimal."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    _add_textbox(slide, Cm(2), Cm(1.2), Cm(30), Cm(2),
                 title, font_size=28, bold=True, color=DARK_TEXT)

    _add_divider(slide, Cm(2), Cm(3.2), Cm(5))

    _add_multiline_textbox(slide, Cm(2.5), Cm(4), Cm(29), Cm(14),
                           body_lines, font_size=15, color=DARK_TEXT)


def _slide_image_text(prs: Presentation, title: str,
                      body_lines: list[str], img_path: Path | None) -> None:
    """Slide with large image on left, concise text on right."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    _add_textbox(slide, Cm(2), Cm(1.2), Cm(30), Cm(2),
                 title, font_size=24, bold=True, color=DARK_TEXT)

    # Large image on left
    if img_path and img_path.exists():
        try:
            slide.shapes.add_picture(
                str(img_path), Cm(1.5), Cm(3.5), Cm(14), Cm(8),
            )
        except Exception:
            pass

    # Concise text on right
    _add_multiline_textbox(slide, Cm(17), Cm(3.5), Cm(15.5), Cm(14),
                           body_lines, font_size=14, color=DARK_TEXT,
                           line_spacing=1.6)


def _slide_conclusion(prs: Presentation, items: list[str]) -> None:
    """Next steps — numbered list, clean."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    _add_textbox(slide, Cm(2), Cm(1.5), Cm(30), Cm(2.5),
                 "Next Steps", font_size=36, bold=True, color=DARK_TEXT)

    _add_divider(slide, Cm(2), Cm(3.8), Cm(5))

    numbered_lines = [f"{i+1}.  {item}" for i, item in enumerate(items)]
    _add_multiline_textbox(slide, Cm(3), Cm(5), Cm(28), Cm(13),
                           numbered_lines, font_size=18, color=DARK_TEXT,
                           line_spacing=1.8)

    # Bottom accent bar
    bar = slide.shapes.add_shape(1, Cm(0), Cm(18.65), SLIDE_WIDTH, Cm(0.4))
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()


# ---------------------------------------------------------------------------
# News card slides
# ---------------------------------------------------------------------------


def _slides_news_card(prs: Presentation, card: EduNewsCard, idx: int) -> None:
    """Generate slides per news card — one idea per slide."""
    if not card.is_valid_news:
        _slide_text(prs, f"#{idx} — 無效內容", [
            f"判定：{card.invalid_reason or '非新聞內容'}",
            "",
            f"原因：{card.invalid_cause or '資料抓取異常'}",
            f"處理建議：{card.invalid_fix or '調整來源設定'}",
        ])
        return

    # Get image
    try:
        img_path = get_news_image(card.title_plain, card.category)
    except Exception:
        img_path = None

    # Slide: image + key summary (minimal text)
    body = [
        card.what_happened[:100],
        "",
        f"為何重要：{card.why_important[:80]}",
        "",
        f"建議行動：{(card.action_items[0][:60]) if card.action_items else '持續觀察'}",
    ]
    _slide_image_text(prs, f"#{idx}  {card.title_plain[:35]}", body, img_path)


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
    """Generate a Notion-style white-background PPTX executive report."""
    log = get_logger()
    if output_path is None:
        project_root = Path(__file__).resolve().parent.parent
        output_path = project_root / "outputs" / "executive_report.pptx"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    prs = Presentation()
    prs.slide_width = SLIDE_WIDTH
    prs.slide_height = SLIDE_HEIGHT

    # --- Cover ---
    _slide_cover(prs, report_time)

    # --- Key Takeaways (3-5 bullets) ---
    _slide_key_takeaways(prs, cards, health, total_items)

    # --- Overview Table ---
    _slide_overview_table(prs, cards)

    # --- Per-news slides (image left, text right, minimal) ---
    valid_cards = [c for c in cards if c.is_valid_news]
    invalid_cards = [c for c in cards if not c.is_valid_news]

    if valid_cards:
        _slide_section(prs, "新聞深度解析", "News Analysis")
        for i, card in enumerate(valid_cards, 1):
            _slides_news_card(prs, card, i)

    # --- Decision Summary Table ---
    if valid_cards:
        decision_rows = []
        for i, c in enumerate(valid_cards[:8], 1):
            effect = c.derivable_effects[0][:25] if c.derivable_effects else "待評估"
            risk = c.speculative_effects[0][:25] if c.speculative_effects else "低"
            action = c.action_items[0][:30] if c.action_items else "持續觀察"
            decision_rows.append([
                str(i), c.title_plain[:20], effect, risk, action,
            ])
        _add_table_slide(
            prs, "決策摘要表  Decision Matrix",
            ["#", "事件", "影響", "風險", "建議行動"],
            decision_rows,
        )

    # --- Invalid items (if any) ---
    for i, card in enumerate(invalid_cards, len(valid_cards) + 1):
        _slides_news_card(prs, card, i)

    # --- System Status ---
    metrics_lines = [
        f"資料完整率：{health.success_rate:.0f}%",
        f"中位數延遲：{health.p50_latency:.1f}s",
        f"高延遲指標：{health.p95_latency:.1f}s",
        f"雜訊清除：{health.entity_noise_removed} 筆",
        "",
        f"整體狀態：{health.traffic_light_emoji} {health.traffic_light_label}",
    ]
    if health.fail_reasons:
        metrics_lines.append("")
        for reason, count in health.fail_reasons.items():
            metrics_lines.append(f"  {reason}：{count} 次")
    _slide_text(prs, "系統運作概況", metrics_lines)

    # --- Next Steps ---
    next_items = [
        "檢視今日新聞與業務的關聯性",
        "針對高風險事件指派追蹤負責人",
        "回顧過去一週趨勢，識別重複模式",
        "確認系統運作狀態，必要時調整來源",
    ]
    _slide_conclusion(prs, next_items)

    prs.save(str(output_path))
    log.info("Executive PPTX generated: %s", output_path)
    return output_path
