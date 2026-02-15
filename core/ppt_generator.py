"""PPTX CEO Motion Slides — Dark Theme 簡報生成器。

深色背景、黃色高亮、白色內文。
每則新聞兩頁：Page 1 WHAT HAPPENED + Page 2 WHY IT MATTERS (Q&A)。
色彩系統：#121218 深色背景 + #FFD600 黃色高亮 + #E65A37 橘色 accent。
含數據卡、CEO 比喻、Video Reference、Sources。

禁用詞彙：ai捕捉、AI Intel、Z1~Z5、pipeline、ETL、verify_run、ingestion、ai_core
禁用系統運作字眼：系統健康、資料可信度、延遲、P95、雜訊清除、健康狀態
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Cm, Pt

from core.content_strategy import (
    build_ceo_actions,
    build_ceo_brief_blocks,
    build_corp_watch_summary,
    build_decision_card,
    build_signal_summary,
    build_structured_executive_summary,
    compute_market_heat,
    is_non_event_or_index,
    sanitize,
    score_event_impact,
)
from core.image_helper import get_news_image
from schemas.education_models import (
    EduNewsCard,
    SystemHealthReport,
)
from utils.logger import get_logger

# ---------------------------------------------------------------------------
# CEO Motion Slides — Dark Theme colour palette
# ---------------------------------------------------------------------------
DARK_BG = RGBColor(0x12, 0x12, 0x18)           # #121218
DARK_TEXT = RGBColor(0xF0, 0xF0, 0xF0)         # #F0F0F0
DARK_MUTED = RGBColor(0x64, 0x64, 0x6E)        # #64646E
DARK_CARD = RGBColor(0x1C, 0x1C, 0x24)         # #1C1C24

LIGHT_BG = RGBColor(0xFF, 0xFF, 0xFF)          # #FFFFFF
LIGHT_TEXT = RGBColor(0x22, 0x28, 0x33)        # #222833
LIGHT_MUTED = RGBColor(0x5E, 0x67, 0x73)       # #5E6773
LIGHT_CARD = RGBColor(0xF2, 0xF4, 0xF8)        # #F2F4F8
LIGHT_TABLE_HEADER_BG = RGBColor(0x2D, 0x36, 0x3F)  # high contrast on light theme

HIGHLIGHT_YELLOW = RGBColor(0xFF, 0xD6, 0x00)  # #FFD600
ACCENT = RGBColor(0xE6, 0x5A, 0x37)            # #E65A37

# Backward-compatible exported names used by existing tests/imports.
BG_DARK = DARK_BG
TEXT_WHITE = DARK_TEXT
SUBTLE_GRAY = DARK_MUTED
CARD_BG = DARK_CARD

SLIDE_WIDTH = Cm(33.867)   # 16:9 default
SLIDE_HEIGHT = Cm(19.05)
MIN_FONT_SIZE_PT = 20
MIN_LINE_SPACING = 1.15

TABLE_HEADER_BG = DARK_CARD
TABLE_HEADER_TEXT = DARK_TEXT


def _coerce_font_size(font_size: int | float) -> int:
    return max(int(font_size), MIN_FONT_SIZE_PT)


def _apply_theme(theme: str) -> None:
    """Apply runtime palette for the current deck generation."""
    global BG_DARK, TEXT_WHITE, SUBTLE_GRAY, CARD_BG, TABLE_HEADER_BG, TABLE_HEADER_TEXT
    choice = (theme or "light").strip().lower()
    if choice == "light":
        BG_DARK = LIGHT_BG
        TEXT_WHITE = LIGHT_TEXT
        SUBTLE_GRAY = LIGHT_MUTED
        CARD_BG = LIGHT_CARD
        TABLE_HEADER_BG = LIGHT_TABLE_HEADER_BG
        TABLE_HEADER_TEXT = RGBColor(0xFF, 0xFF, 0xFF)
    elif choice == "dark":
        BG_DARK = DARK_BG
        TEXT_WHITE = DARK_TEXT
        SUBTLE_GRAY = DARK_MUTED
        CARD_BG = DARK_CARD
        TABLE_HEADER_BG = DARK_CARD
        TABLE_HEADER_TEXT = DARK_TEXT
    else:
        raise ValueError(f"Unsupported theme: {theme}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def safe_text(text: str, limit: int = 200) -> str:
    """Sanitize text; truncate at word boundary if needed."""
    t = sanitize(text)
    if len(t) <= limit:
        return t
    cut = t[:limit]
    for sep in ["。", ". ", "，", " "]:
        pos = cut.rfind(sep)
        if pos > limit * 0.6:
            return cut[:pos + len(sep)].rstrip()
    return cut + "…"


def _set_slide_bg(slide, color: RGBColor = None) -> None:
    if color is None:
        color = BG_DARK
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = color


def _add_textbox(
    slide, left, top, width, height, text: str,
    font_size: int = 18, color: RGBColor = None,
    bold: bool = False, alignment: PP_ALIGN = PP_ALIGN.LEFT,
) -> None:
    if color is None:
        color = TEXT_WHITE
    text_size = _coerce_font_size(font_size)
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    p = tf.paragraphs[0]
    p.text = safe_text(text)
    p.font.size = Pt(text_size)
    p.font.color.rgb = color
    p.font.bold = bold
    p.alignment = alignment
    p.line_spacing = MIN_LINE_SPACING


def _add_multiline_textbox(
    slide, left, top, width, height, lines: list[str],
    font_size: int = 14, color: RGBColor = None,
    bold_first: bool = False, line_spacing: float = 1.5,
) -> None:
    if color is None:
        color = TEXT_WHITE
    text_size = _coerce_font_size(font_size)
    spacing = max(float(line_spacing), MIN_LINE_SPACING)
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = safe_text(line)
        p.font.size = Pt(text_size)
        p.font.color.rgb = color
        if bold_first and i == 0:
            p.font.bold = True
        p.line_spacing = spacing
        p.space_after = Pt(text_size * (spacing - 1))


def _add_highlight_textbox(
    slide, left, top, width, height,
    prefix: str, highlight: str, suffix: str = "",
    font_size: int = 18,
) -> None:
    """Add a textbox with keyword in yellow bold (HIGHLIGHT_YELLOW)."""
    text_size = _coerce_font_size(font_size)
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    p = tf.paragraphs[0]
    p.line_spacing = MIN_LINE_SPACING

    if prefix:
        run_pre = p.add_run()
        run_pre.text = safe_text(prefix)
        run_pre.font.size = Pt(text_size)
        run_pre.font.color.rgb = TEXT_WHITE

    run_hl = p.add_run()
    run_hl.text = safe_text(highlight)
    run_hl.font.size = Pt(text_size)
    run_hl.font.color.rgb = HIGHLIGHT_YELLOW
    run_hl.font.bold = True

    if suffix:
        run_suf = p.add_run()
        run_suf.text = safe_text(suffix)
        run_suf.font.size = Pt(text_size)
        run_suf.font.color.rgb = TEXT_WHITE


def _add_divider(slide, left, top, width, color: RGBColor = None) -> None:
    if color is None:
        color = ACCENT
    line = slide.shapes.add_shape(1, left, top, width, Cm(0.05))
    line.fill.solid()
    line.fill.fore_color.rgb = color
    line.line.fill.background()


def _add_table_slide(prs: Presentation, title: str,
                     headers: list[str], rows: list[list[str]]) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)
    _add_textbox(slide, Cm(2), Cm(1.2), Cm(30), Cm(2),
                 title, font_size=28, bold=True, color=HIGHLIGHT_YELLOW)
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
            p.font.size = Pt(_coerce_font_size(10))
            p.font.bold = True
            p.font.color.rgb = TABLE_HEADER_TEXT
            p.line_spacing = MIN_LINE_SPACING
        cell.fill.solid()
        cell.fill.fore_color.rgb = TABLE_HEADER_BG
    for ri, row_data in enumerate(rows):
        for ci, val in enumerate(row_data):
            cell = tbl.cell(ri + 1, ci)
            cell.text = safe_text(val)
            for p in cell.text_frame.paragraphs:
                p.font.size = Pt(_coerce_font_size(9))
                p.font.color.rgb = TEXT_WHITE
                p.line_spacing = MIN_LINE_SPACING
            cell.fill.solid()
            cell.fill.fore_color.rgb = BG_DARK


# ---------------------------------------------------------------------------
# Slide builders
# ---------------------------------------------------------------------------


def _slide_cover(prs: Presentation, report_time: str) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)
    # Cover banner image
    try:
        img_path = get_news_image("Daily Tech Intelligence Briefing", "科技")
        if img_path and img_path.exists():
            slide.shapes.add_picture(
                str(img_path), Cm(0), Cm(0), SLIDE_WIDTH, Cm(7),
            )
    except Exception:
        pass
    _add_divider(slide, Cm(0), Cm(7.2), SLIDE_WIDTH, color=ACCENT)
    _add_textbox(slide, Cm(4), Cm(8), Cm(26), Cm(3.5),
                 "CEO Decision Brief", font_size=44, bold=True,
                 color=HIGHLIGHT_YELLOW, alignment=PP_ALIGN.CENTER)
    _add_textbox(slide, Cm(4), Cm(11.5), Cm(26), Cm(2),
                 "每日科技趨勢簡報", font_size=20,
                 color=SUBTLE_GRAY, alignment=PP_ALIGN.CENTER)
    _add_divider(slide, Cm(15.5), Cm(14), Cm(3), color=ACCENT)
    _add_textbox(slide, Cm(4), Cm(15), Cm(26), Cm(1.5),
                 report_time, font_size=14, color=SUBTLE_GRAY,
                 alignment=PP_ALIGN.CENTER)


def _slide_structured_summary(prs: Presentation, cards: list[EduNewsCard]) -> None:
    """Structured Executive Summary — 5 sections, yellow titles, white content."""
    summary = build_structured_executive_summary(cards)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    # Page title
    _add_textbox(slide, Cm(2), Cm(0.8), Cm(30), Cm(1.5),
                 "Structured Summary", font_size=32, bold=True,
                 color=HIGHLIGHT_YELLOW)
    _add_divider(slide, Cm(2), Cm(2.3), Cm(4), color=ACCENT)

    section_map = [
        ("AI Trends", summary.get("ai_trends", [])),
        ("Tech Landing", summary.get("tech_landing", [])),
        ("Market Competition", summary.get("market_competition", [])),
        ("Opportunities & Risks", summary.get("opportunities_risks", [])),
        ("Recommended Actions", summary.get("recommended_actions", [])),
    ]

    y = 3.0
    for sec_title, items in section_map:
        # Section title in yellow
        _add_textbox(slide, Cm(2), Cm(y), Cm(30), Cm(1),
                     sec_title, font_size=14, bold=True,
                     color=HIGHLIGHT_YELLOW)
        y += 1.0
        # Content in white
        for item in items[:2]:
            _add_textbox(slide, Cm(3), Cm(y), Cm(28), Cm(0.8),
                         f"• {safe_text(item, 80)}", font_size=11,
                         color=TEXT_WHITE)
            y += 0.8
        y += 0.3


def _slide_key_takeaways(prs: Presentation, cards: list[EduNewsCard],
                         total_items: int) -> None:
    """Key takeaways slide."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)
    _add_textbox(slide, Cm(2), Cm(1.2), Cm(30), Cm(2),
                 "Key Takeaways", font_size=36, bold=True,
                 color=HIGHLIGHT_YELLOW)
    _add_divider(slide, Cm(2), Cm(3.2), Cm(4), color=ACCENT)

    event_cards = [c for c in cards if c.is_valid_news and not is_non_event_or_index(c)]

    takeaways: list[str] = []
    takeaways.append(f"本日分析 {total_items} 則，{len(event_cards)} 則值得關注")
    for c in event_cards[:3]:
        dc = build_decision_card(c)
        takeaways.append(f"{safe_text(c.title_plain, 35)} — {dc['event']}")
    if not event_cards:
        takeaways.append("本日無重大事件需要決策")

    _add_multiline_textbox(
        slide, Cm(3), Cm(4.5), Cm(28), Cm(13),
        takeaways, font_size=18, color=TEXT_WHITE, line_spacing=1.8,
    )


def _slide_overview_table(prs: Presentation, cards: list[EduNewsCard]) -> None:
    event_cards = [c for c in cards if c.is_valid_news and not is_non_event_or_index(c)]
    if not event_cards:
        return
    headers = ["#", "標題", "類別", "評分"]
    rows = []
    for i, c in enumerate(event_cards[:8], 1):
        rows.append([
            str(i), safe_text(c.title_plain, 35),
            c.category or "綜合", f"{c.final_score:.1f}",
        ])
    _add_table_slide(prs, "今日總覽  Overview", headers, rows)


def _slide_brief_page1(prs: Presentation, card: EduNewsCard, idx: int) -> None:
    """WHAT HAPPENED slide — event badge, title, AI trend, hero image,
    event liner, data card, CEO metaphor."""
    brief = build_ceo_brief_blocks(card)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    # Event badge number
    _add_textbox(slide, Cm(1.5), Cm(0.5), Cm(3), Cm(1.2),
                 f"#{idx}", font_size=28, bold=True,
                 color=ACCENT)

    # Title (≤14 chars)
    _add_textbox(slide, Cm(4.5), Cm(0.5), Cm(26), Cm(1.2),
                 brief["title"], font_size=22, bold=True,
                 color=TEXT_WHITE)

    # AI trend liner (yellow)
    _add_textbox(slide, Cm(2), Cm(1.8), Cm(30), Cm(1),
                 brief["ai_trend_liner"], font_size=12,
                 color=HIGHLIGHT_YELLOW)

    # Hero image (full width)
    text_top = Cm(3.0)
    try:
        img_path = get_news_image(card.title_plain, card.category)
        if img_path and img_path.exists():
            slide.shapes.add_picture(
                str(img_path), Cm(0), Cm(3.0), SLIDE_WIDTH, Cm(5.5),
            )
            text_top = Cm(8.8)
    except Exception:
        pass

    # Event one-liner
    _add_textbox(slide, Cm(2), text_top, Cm(30), Cm(1.2),
                 brief["event_liner"], font_size=14,
                 color=TEXT_WHITE)

    y_cursor = text_top.cm + 1.5

    # Data card → large yellow numbers
    data_items = brief.get("data_card", [])
    if data_items:
        for item in data_items[:2]:
            _add_textbox(slide, Cm(2), Cm(y_cursor), Cm(10), Cm(1.5),
                         item["value"], font_size=32, bold=True,
                         color=HIGHLIGHT_YELLOW)
            _add_textbox(slide, Cm(13), Cm(y_cursor + 0.3), Cm(18), Cm(1),
                         item["label"], font_size=12,
                         color=SUBTLE_GRAY)
            y_cursor += 1.8

    # CEO metaphor (italic style — using subtle gray)
    metaphor = brief.get("ceo_metaphor", "")
    if metaphor:
        txBox = slide.shapes.add_textbox(
            Cm(2), Cm(min(y_cursor, 16.5)), Cm(30), Cm(1.5))
        tf = txBox.text_frame
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = safe_text(metaphor, 150)
        run.font.size = Pt(_coerce_font_size(12))
        run.font.color.rgb = SUBTLE_GRAY
        run.font.italic = True


def _slide_brief_page2(prs: Presentation, card: EduNewsCard, idx: int) -> None:
    """WHY IT MATTERS (Q&A) slide — Q1/Q2/Q3, video reference, sources."""
    brief = build_ceo_brief_blocks(card)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    # Page header
    _add_textbox(slide, Cm(2), Cm(0.5), Cm(28), Cm(1.2),
                 f"#{idx}  WHY IT MATTERS", font_size=22, bold=True,
                 color=HIGHLIGHT_YELLOW)
    _add_divider(slide, Cm(2), Cm(1.8), Cm(4), color=ACCENT)

    y = 2.5

    # Q1 — 商業意義
    _add_textbox(slide, Cm(2), Cm(y), Cm(30), Cm(0.8),
                 "Q1：這件事的商業意義？", font_size=14, bold=True,
                 color=HIGHLIGHT_YELLOW)
    y += 1.0
    _add_textbox(slide, Cm(3), Cm(y), Cm(28), Cm(1.5),
                 brief["q1_meaning"], font_size=12,
                 color=TEXT_WHITE)
    y += 1.8

    # Q2 — 對公司影響
    _add_textbox(slide, Cm(2), Cm(y), Cm(30), Cm(0.8),
                 "Q2：對公司的影響？", font_size=14, bold=True,
                 color=HIGHLIGHT_YELLOW)
    y += 1.0
    _add_textbox(slide, Cm(3), Cm(y), Cm(28), Cm(1.5),
                 brief["q2_impact"], font_size=12,
                 color=TEXT_WHITE)
    y += 1.8

    # Q3 — 現在要做什麼 (numbered actions ≤3)
    _add_textbox(slide, Cm(2), Cm(y), Cm(30), Cm(0.8),
                 "Q3：現在要做什麼？", font_size=14, bold=True,
                 color=HIGHLIGHT_YELLOW)
    y += 1.0
    actions = brief.get("q3_actions", [])
    action_lines = [f"{i}. {safe_text(a, 60)}" for i, a in enumerate(actions[:3], 1)]
    _add_multiline_textbox(
        slide, Cm(3), Cm(y), Cm(28), Cm(2.5),
        action_lines, font_size=12, color=TEXT_WHITE, line_spacing=1.4,
    )
    y += max(len(action_lines) * 0.9, 1.5) + 0.5

    # Divider before bottom section
    _add_divider(slide, Cm(2), Cm(min(y, 15.5)), Cm(30), color=SUBTLE_GRAY)
    y = min(y + 0.5, 16.0)

    # Video reference
    videos = brief.get("video_source", [])
    if videos:
        vid = videos[0]
        _add_textbox(slide, Cm(2), Cm(y), Cm(30), Cm(0.7),
                     f"Video: {safe_text(vid.get('title', ''), 50)}",
                     font_size=10, color=SUBTLE_GRAY)
        y += 0.7
        _add_textbox(slide, Cm(2), Cm(y), Cm(30), Cm(0.5),
                     vid.get("url", ""), font_size=8, color=SUBTLE_GRAY)
        y += 0.7

    # Sources
    sources = brief.get("sources", [])
    if sources:
        _add_textbox(slide, Cm(2), Cm(min(y, 17.5)), Cm(30), Cm(0.7),
                     f"Source: {safe_text(sources[0], 80)}",
                     font_size=9, color=SUBTLE_GRAY)


def _slide_signal_thermometer(prs: Presentation, cards: list[EduNewsCard]) -> None:
    """Signal Thermometer — market heat gauge + signal type breakdown."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    # Title
    _add_textbox(slide, Cm(2), Cm(0.8), Cm(30), Cm(1.5),
                 "Signal Thermometer", font_size=32, bold=True,
                 color=HIGHLIGHT_YELLOW)
    _add_divider(slide, Cm(2), Cm(2.3), Cm(4), color=ACCENT)

    # Market heat index
    heat = compute_market_heat(cards)
    heat_color = ACCENT if heat["level"] in ("VERY_HIGH", "HIGH") else HIGHLIGHT_YELLOW
    _add_textbox(slide, Cm(2), Cm(3.0), Cm(10), Cm(2.5),
                 str(heat["score"]), font_size=72, bold=True,
                 color=heat_color)
    _add_textbox(slide, Cm(12), Cm(3.2), Cm(20), Cm(1),
                 f"Market Heat Index — {heat['trend_word']}",
                 font_size=16, color=TEXT_WHITE)
    _add_textbox(slide, Cm(12), Cm(4.5), Cm(20), Cm(1),
                 f"Level: {heat['level']}", font_size=12, color=SUBTLE_GRAY)

    # Signal breakdown
    signals = build_signal_summary(cards)
    y = 6.5
    _add_textbox(slide, Cm(2), Cm(y), Cm(30), Cm(1),
                 "Top Signals", font_size=18, bold=True,
                 color=HIGHLIGHT_YELLOW)
    y += 1.3

    heat_colors = {"hot": ACCENT, "warm": HIGHLIGHT_YELLOW, "cool": SUBTLE_GRAY}

    for sig in signals[:3]:
        heat_word = str(sig.get("heat", "cool"))
        sig_color = heat_colors.get(heat_word, SUBTLE_GRAY)
        signal_text = str(sig.get("signal_text", sig.get("title", "")))
        platform_count = int(sig.get("platform_count", sig.get("source_count", 0)))
        heat_score = int(sig.get("heat_score", 0))
        label = str(sig.get("label", sig.get("signal_type", "Signal")))
        # Signal type badge
        _add_textbox(slide, Cm(2), Cm(y), Cm(8), Cm(0.8),
                     label, font_size=12, bold=True,
                     color=sig_color)
        # signal_text + required fallback fields
        _add_textbox(slide, Cm(10), Cm(y), Cm(20), Cm(0.8),
                     f"{signal_text}  | platform_count={platform_count} | heat_score={heat_score}",
                     font_size=11, color=TEXT_WHITE)
        # Heat badge
        _add_textbox(slide, Cm(30), Cm(y), Cm(2), Cm(0.8),
                     heat_word.upper(), font_size=10, bold=True,
                     color=sig_color)
        y += 1.2


def _slide_corp_watch(
    prs: Presentation,
    cards: list[EduNewsCard],
    metrics: dict | None = None,
) -> None:
    """Corp Watch — Tier A + Tier B company monitoring."""
    corp = build_corp_watch_summary(cards, metrics=metrics)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    # Title
    _add_textbox(slide, Cm(2), Cm(0.8), Cm(30), Cm(1.5),
                 "Corp Watch", font_size=32, bold=True,
                 color=HIGHLIGHT_YELLOW)
    _add_divider(slide, Cm(2), Cm(2.3), Cm(4), color=ACCENT)

    # Total mentions
    _add_textbox(slide, Cm(2), Cm(3.0), Cm(30), Cm(1),
                 f"Total Mentions: {corp['total_mentions']}",
                 font_size=14, color=TEXT_WHITE)
    _add_textbox(slide, Cm(2), Cm(3.8), Cm(30), Cm(0.8),
                 str(corp.get("status_message", "")),
                 font_size=12, color=SUBTLE_GRAY)

    # v5.1 no-event fallback
    if corp.get("updates", corp["total_mentions"]) == 0:
        fail_bits = []
        for item in corp.get("top_fail_reasons", []):
            reason = str(item.get("reason", "unknown"))
            count = int(item.get("count", 0))
            fail_bits.append(f"{reason} ({count})")
        fail_text = ", ".join(fail_bits) if fail_bits else "-"

        _add_textbox(slide, Cm(2), Cm(4.5), Cm(30), Cm(1),
                     "Source Scan Stats", font_size=16, bold=True,
                     color=HIGHLIGHT_YELLOW)
        _add_multiline_textbox(
            slide, Cm(3), Cm(5.7), Cm(28), Cm(8),
            [
                f"status: {corp.get('status_message', '-')}",
                f"sources_total: {corp.get('sources_total', 0)}",
                f"success_count: {corp.get('success_count', 0)}",
                f"fail_count: {corp.get('fail_count', 0)}",
                f"top_fail_reasons: {fail_text}",
            ],
            font_size=12,
            color=TEXT_WHITE,
            line_spacing=1.5,
        )
        return

    y = 4.5

    # Tier A
    _add_textbox(slide, Cm(2), Cm(y), Cm(30), Cm(1),
                 "Tier A — Global Leaders", font_size=16, bold=True,
                 color=HIGHLIGHT_YELLOW)
    y += 1.2

    if corp["tier_a"]:
        for item in corp["tier_a"][:5]:
            _add_highlight_textbox(
                slide, Cm(3), Cm(y), Cm(28), Cm(0.8),
                f"{item['name']}  ", item["impact_label"],
                f"  {safe_text(item['event_title'], 30)}",
                font_size=11,
            )
            y += 1.0
    else:
        _add_textbox(slide, Cm(3), Cm(y), Cm(28), Cm(0.8),
                     "今日無 Tier A 公司相關事件", font_size=11,
                     color=SUBTLE_GRAY)
        y += 1.0

    y += 0.5

    # Tier B
    _add_textbox(slide, Cm(2), Cm(y), Cm(30), Cm(1),
                 "Tier B — Asia Leaders", font_size=16, bold=True,
                 color=HIGHLIGHT_YELLOW)
    y += 1.2

    if corp["tier_b"]:
        for item in corp["tier_b"][:4]:
            _add_highlight_textbox(
                slide, Cm(3), Cm(y), Cm(28), Cm(0.8),
                f"{item['name']}  ", item["impact_label"],
                f"  {safe_text(item['event_title'], 30)}",
                font_size=11,
            )
            y += 1.0
    else:
        _add_textbox(slide, Cm(3), Cm(y), Cm(28), Cm(0.8),
                     "今日無 Tier B 公司相關事件", font_size=11,
                     color=SUBTLE_GRAY)


def _slide_event_ranking(prs: Presentation, cards: list[EduNewsCard]) -> None:
    """Event Ranking — impact-scored event list with color-coded badges."""
    event_cards = [c for c in cards if c.is_valid_news and not is_non_event_or_index(c)]
    if not event_cards:
        return

    # Score and sort
    scored = []
    for c in event_cards[:8]:
        impact = score_event_impact(c)
        scored.append((c, impact))
    scored.sort(key=lambda x: x[1]["impact"], reverse=True)

    headers = ["Rank", "Impact", "標題", "類別", "Action"]
    rows = []
    for rank, (c, imp) in enumerate(scored, 1):
        dc = build_decision_card(c)
        action = dc["actions"][0] if dc["actions"] else "待確認"
        rows.append([
            str(rank),
            f"{imp['impact']}/5 {imp['label']}",
            safe_text(c.title_plain, 25),
            c.category or "綜合",
            safe_text(action, 25),
        ])
    _add_table_slide(prs, "Event Ranking  事件影響力排行", headers, rows)


def _slide_recommended_moves(prs: Presentation, cards: list[EduNewsCard]) -> None:
    """Recommended Moves — MOVE (red) / TEST (yellow) / WATCH (gray) actions."""
    actions = build_ceo_actions(cards)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    # Title
    _add_textbox(slide, Cm(2), Cm(0.8), Cm(30), Cm(1.5),
                 "Recommended Moves", font_size=32, bold=True,
                 color=HIGHLIGHT_YELLOW)
    _add_divider(slide, Cm(2), Cm(2.3), Cm(4), color=ACCENT)

    type_colors = {
        "MOVE": ACCENT,
        "TEST": HIGHLIGHT_YELLOW,
        "WATCH": SUBTLE_GRAY,
    }

    y = 3.5
    if not actions:
        _add_textbox(slide, Cm(3), Cm(y), Cm(28), Cm(1),
                     "本日無需要立即行動的事項", font_size=14,
                     color=TEXT_WHITE)
        return

    for act in actions[:6]:
        tag_color = type_colors.get(act["action_type"], SUBTLE_GRAY)

        # Action type badge
        _add_textbox(slide, Cm(2), Cm(y), Cm(4), Cm(0.9),
                     act["action_type"], font_size=14, bold=True,
                     color=tag_color)
        # Title
        _add_textbox(slide, Cm(6.5), Cm(y), Cm(14), Cm(0.9),
                     act["title"], font_size=12, color=TEXT_WHITE)
        # Detail
        _add_textbox(slide, Cm(6.5), Cm(y + 0.8), Cm(20), Cm(0.7),
                     act["detail"], font_size=10, color=SUBTLE_GRAY)
        # Owner
        _add_textbox(slide, Cm(27), Cm(y), Cm(5), Cm(0.9),
                     act["owner"], font_size=10, color=SUBTLE_GRAY,
                     alignment=PP_ALIGN.RIGHT)
        y += 2.0


def _slide_pending_decisions(prs: Presentation, cards: list[EduNewsCard]) -> None:
    """Last slide: pending decisions & owners."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)
    _add_textbox(slide, Cm(2), Cm(1.5), Cm(30), Cm(2.5),
                 "待決事項與 Owner", font_size=36, bold=True,
                 color=HIGHLIGHT_YELLOW)
    _add_divider(slide, Cm(2), Cm(3.8), Cm(4), color=ACCENT)

    items: list[str] = []
    event_cards = [c for c in cards if c.is_valid_news and not is_non_event_or_index(c)]
    for i, c in enumerate(event_cards[:5], 1):
        dc = build_decision_card(c)
        action = dc["actions"][0] if dc["actions"] else "待確認"
        owner = dc["owner"]
        items.append(f"{i}. {safe_text(action, 80)} → Owner: {owner}")

    if not items:
        items.append("1. 本日無待決事項")

    _add_multiline_textbox(slide, Cm(3), Cm(5), Cm(28), Cm(13),
                           items, font_size=18, color=TEXT_WHITE,
                           line_spacing=1.8)
    _add_divider(slide, Cm(0), Cm(18.7), SLIDE_WIDTH, color=ACCENT)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_executive_ppt(
    cards: list[EduNewsCard],
    health: SystemHealthReport,
    report_time: str,
    total_items: int,
    output_path: Path | None = None,
    theme: str = "light",
    metrics: dict | None = None,
) -> Path:
    log = get_logger()
    if output_path is None:
        project_root = Path(__file__).resolve().parent.parent
        output_path = project_root / "outputs" / "executive_report.pptx"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _apply_theme(theme)

    prs = Presentation()
    prs.slide_width = SLIDE_WIDTH
    prs.slide_height = SLIDE_HEIGHT

    # 1. Cover
    _slide_cover(prs, report_time)

    # 2. Structured Summary (5 sections)
    _slide_structured_summary(prs, cards)

    # 3. Signal Thermometer (v5)
    _slide_signal_thermometer(prs, cards)

    # 4. Corp Watch (v5)
    _slide_corp_watch(prs, cards, metrics=metrics)

    # 5. Key Takeaways
    _slide_key_takeaways(prs, cards, total_items)

    # 6. Overview Table
    _slide_overview_table(prs, cards)

    # 7. Event Ranking (v5)
    _slide_event_ranking(prs, cards)

    # Filter: only event cards for the CEO deck
    event_cards = [c for c in cards if c.is_valid_news and not is_non_event_or_index(c)]

    # 8. Per-event: brief_page1 + brief_page2
    for i, card in enumerate(event_cards, 1):
        _slide_brief_page1(prs, card, i)
        _slide_brief_page2(prs, card, i)

    # 9. Recommended Moves (v5)
    _slide_recommended_moves(prs, cards)

    # 10. Decision Matrix (6 columns)
    if event_cards:
        decision_rows = []
        for i, c in enumerate(event_cards[:8], 1):
            dc = build_decision_card(c)
            decision_rows.append([
                str(i),
                safe_text(dc["event"], 18),
                safe_text(dc["effects"][0], 25) if dc["effects"] else "缺口",
                safe_text(dc["risks"][0], 25) if dc["risks"] else "缺口",
                safe_text(dc["actions"][0], 30) if dc["actions"] else "待確認",
                dc["owner"],
            ])
        _add_table_slide(
            prs, "決策摘要表  Decision Matrix",
            ["#", "事件", "影響", "風險", "建議行動", "要問誰"],
            decision_rows,
        )

    # 11. Pending Decisions
    _slide_pending_decisions(prs, cards)

    prs.save(str(output_path))
    log.info("Executive PPTX generated: %s", output_path)
    return output_path
