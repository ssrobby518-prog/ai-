"""PPTX 總經理版簡報生成器 — Notion 文檔式排版。

白底、左對齊、大留白、細分隔線、無厚重色塊。
每則新聞一頁一概念；圖片上方全寬 banner + 下方文字。
色彩系統：#212838 深藍文字 + #E65A37 橘色 accent。
含嵌入圖片、6 欄決策卡、決策摘要表格。

禁用詞彙：ai捕捉、AI Intel、Z1~Z5、pipeline、ETL、verify_run、ingestion、ai_core
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Cm, Pt

from core.content_strategy import (
    build_decision_card,
    sanitize,
)
from core.image_helper import get_news_image
from schemas.education_models import (
    EduNewsCard,
    SystemHealthReport,
    translate_fail_reason,
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
    p = tf.paragraphs[0]
    p.text = sanitize(text)
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
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = sanitize(line)
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
        cell.text = sanitize(h)
        for p in cell.text_frame.paragraphs:
            p.font.size = Pt(10)
            p.font.bold = True
            p.font.color.rgb = DARK_TEXT
        cell.fill.solid()
        cell.fill.fore_color.rgb = TABLE_HEADER_BG
    for ri, row_data in enumerate(rows):
        for ci, val in enumerate(row_data):
            cell = tbl.cell(ri + 1, ci)
            cell.text = sanitize(val)
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
        takeaways.append(f"{sanitize(c.title_plain[:30])} — {dc['event']}")
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
            str(i), sanitize(c.title_plain[:30]),
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
    """Last slide: pending decisions & owners (not 'Next Steps' teaching tone)."""
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
        items.append(f"{i}. {action} → Owner: {owner}")

    if not items:
        items.append("1. 本日無待決事項")

    _add_multiline_textbox(slide, Cm(3), Cm(5), Cm(28), Cm(13),
                           items, font_size=18, color=DARK_TEXT,
                           line_spacing=1.8)
    _add_divider(slide, Cm(0), Cm(18.7), SLIDE_WIDTH, color=ACCENT)


# ---------------------------------------------------------------------------
# News card slides — full 6-column decision card
# ---------------------------------------------------------------------------


def _slides_news_card(prs: Presentation, card: EduNewsCard, idx: int) -> None:
    if not card.is_valid_news:
        _slide_text(prs, f"#{idx} — 無效內容", [
            f"判定：{card.invalid_reason or '非新聞內容'}",
            "", f"原因：{card.invalid_cause or '資料抓取異常'}",
            f"處理建議：{card.invalid_fix or '調整來源設定'}",
        ])
        return

    try:
        img_path = get_news_image(card.title_plain, card.category)
    except Exception:
        img_path = None

    dc = build_decision_card(card)

    body: list[str] = []
    body.append(f"事件：{dc['event']}")
    body.append("")
    body.append("已知事實：")
    for f in dc["facts"]:
        body.append(f"  • {f}")
    body.append("")
    body.append("可能影響：")
    for e in dc["effects"]:
        body.append(f"  • {e}")
    body.append("")
    body.append("主要風險：")
    for r in dc["risks"]:
        body.append(f"  • {r}")
    body.append("")
    body.append(f"建議行動：{dc['actions'][0]}")
    body.append(f"要問誰：{dc['owner']}")

    _slide_image_text(prs, f"#{idx}  {sanitize(card.title_plain[:35])}", body, img_path)


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

    # Decision Summary Table (6 columns, no empty-talk)
    if valid_cards:
        decision_rows = []
        for i, c in enumerate(valid_cards[:8], 1):
            dc = build_decision_card(c)
            decision_rows.append([
                str(i),
                dc["event"][:18],
                dc["effects"][0][:20] if dc["effects"] else "缺口",
                dc["risks"][0][:20] if dc["risks"] else "缺口",
                dc["actions"][0][:25] if dc["actions"] else "待確認",
                dc["owner"],
            ])
        _add_table_slide(
            prs, "決策摘要表  Decision Matrix",
            ["#", "事件", "影響", "風險", "建議行動", "要問誰"],
            decision_rows,
        )

    for i, card in enumerate(invalid_cards, len(valid_cards) + 1):
        _slides_news_card(prs, card, i)

    # System status with management interpretation
    if health.success_rate >= 80:
        cred = f"今日資料可信度良好（完整率 {health.success_rate:.0f}%），決策依據充分"
    elif health.success_rate >= 50:
        cred = f"今日資料可信度中等（完整率 {health.success_rate:.0f}%），部分結論需保守解讀"
    else:
        cred = f"今日資料可信度偏低（完整率 {health.success_rate:.0f}%），交叉驗證後再做決策"

    metrics_lines = [
        cred, "",
        f"處理延遲：中位數 {health.p50_latency:.1f}s｜P95 {health.p95_latency:.1f}s",
        f"雜訊清除：{health.entity_noise_removed} 筆",
        "", f"整體狀態：{health.traffic_light_emoji} {health.traffic_light_label}",
    ]
    if health.fail_reasons:
        metrics_lines.extend(["", "需要處理的風險："])
        for reason, count in sorted(health.fail_reasons.items(), key=lambda x: -x[1])[:2]:
            metrics_lines.append(
                f"  • {translate_fail_reason(reason)}（{count} 次）→ 可能影響資料涵蓋範圍"
            )
    _slide_text(prs, "系統運作概況", metrics_lines)

    _slide_pending_decisions(prs, cards)

    prs.save(str(output_path))
    log.info("Executive PPTX generated: %s", output_path)
    return output_path
