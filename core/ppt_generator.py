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
    get_event_cards_for_deck,
    build_signal_summary,
    build_structured_executive_summary,
    compute_market_heat,
    is_non_event_or_index,
    quality_guard_block,
    sanitize,
    score_event_impact,
    semantic_guard_text,
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
        cell.text = safe_text(h) or h
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
            cell.text = safe_text(val) or val
            for p in cell.text_frame.paragraphs:
                p.font.size = Pt(_coerce_font_size(9))
                p.font.color.rgb = TEXT_WHITE
                p.line_spacing = MIN_LINE_SPACING
            cell.fill.solid()
            cell.fill.fore_color.rgb = CARD_BG


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


def _slide_overview_table(
    prs: Presentation,
    event_cards: list[EduNewsCard],
    cards: list[EduNewsCard] | None = None,
) -> None:
    headers = ["#", "標題", "類別", "評分", "事件"]
    rows = []
    if event_cards:
        for i, c in enumerate(event_cards[:8], 1):
            what_cell = safe_text(c.what_happened or "", 80)
            what_cell = semantic_guard_text(what_cell, c)
            rows.append([
                str(i), safe_text(c.title_plain, 35),
                c.category or "綜合", f"{c.final_score:.1f}",
                what_cell,
            ])
    else:
        signals = build_signal_summary(cards or [])
        headers = ["#", "Signal", "來源", "熱度"]
        for i, sig in enumerate(signals[:3], 1):
            rows.append(
                [
                    str(i),
                    safe_text(str(sig.get("title", sig.get("signal_text", "來源訊號"))), 35),
                    safe_text(str(sig.get("source_url", "") or sig.get("source_name", "scan")), 35),
                    str(int(sig.get("heat_score", 30) or 30)),
                ]
            )
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
    # Filter out empty / hollow action lines (prevents "1. " bullets)
    action_lines = [
        f"{i}. {safe_text(a, 60)}"
        for i, a in enumerate(actions[:3], 1)
        if safe_text(a, 60)
    ]
    if not action_lines:
        action_lines = ["追蹤來源，確認數字證據後決定行動方向。"]
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
        title = str(sig.get("title", signal_text))
        source_name = str(sig.get("source_name", "來源平台"))
        source_url = str(sig.get("source_url", "")).strip()
        # Signal type badge
        _add_textbox(slide, Cm(2), Cm(y), Cm(8), Cm(0.8),
                     label, font_size=12, bold=True,
                     color=sig_color)
        # signal_text + required fallback fields
        _add_textbox(slide, Cm(10), Cm(y), Cm(20), Cm(0.8),
                     f"{title} | platform_count={platform_count} | heat_score={heat_score} | source: {source_name}",
                     font_size=11, color=TEXT_WHITE)
        if source_url.startswith("http"):
            _add_textbox(slide, Cm(10), Cm(y + 0.55), Cm(20), Cm(0.6),
                         source_url, font_size=10, color=SUBTLE_GRAY)
        # Heat badge
        _add_textbox(slide, Cm(30), Cm(y), Cm(2), Cm(0.8),
                     heat_word.upper(), font_size=10, bold=True,
                     color=sig_color)
        y += 1.4


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
        fail_text = ", ".join(fail_bits) if fail_bits else "none"
        source_bits = []
        for src in corp.get("top_sources", [])[:3]:
            source_bits.append(
                f"{src.get('source_name', 'none')}: items_seen={src.get('items_seen', 0)}, "
                f"gate_pass={src.get('gate_pass', 0)}, gate_soft_pass={src.get('gate_soft_pass', 0)}"
            )
        top_sources_text = " | ".join(source_bits) if source_bits else "none"

        _add_textbox(slide, Cm(2), Cm(4.5), Cm(30), Cm(1),
                     "Source Scan Stats", font_size=16, bold=True,
                     color=HIGHLIGHT_YELLOW)
        _add_multiline_textbox(
            slide, Cm(3), Cm(5.7), Cm(28), Cm(8),
            [
                f"status: {corp.get('status_message', 'none')}",
                f"sources_total: {corp.get('sources_total', 0)}",
                f"success_count: {corp.get('success_count', 0)}",
                f"fail_count: {corp.get('fail_count', 0)}",
                f"top_fail_reasons: {fail_text}",
                f"top_sources: {top_sources_text}",
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


def _slide_scan_diagnostics(
    prs: Presentation,
    cards: list[EduNewsCard],
    metrics: dict | None,
    event_cards: list[EduNewsCard],
) -> None:
    """Fixed diagnostics slide to keep base deck flow complete on low-news days."""
    m = metrics or {}
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    _add_textbox(slide, Cm(2), Cm(0.8), Cm(30), Cm(1.5),
                 "Scan Diagnostics", font_size=32, bold=True,
                 color=HIGHLIGHT_YELLOW)
    _add_divider(slide, Cm(2), Cm(2.3), Cm(4), color=ACCENT)

    fetched_total = int(m.get("fetched_total", len(cards)))
    hard_pass_total = int(m.get("hard_pass_total", 0))
    soft_pass_total = int(m.get("soft_pass_total", 0))
    rejected_total = int(m.get("rejected_total", m.get("gate_reject_total", 0)))
    sources_total = int(m.get("sources_total", 0))
    success_count = int(m.get("sources_success", 0))
    fail_count = int(m.get("sources_failed", 0))

    lines = [
        f"fetched_total={fetched_total}",
        f"hard_pass_total={hard_pass_total}",
        f"soft_pass_total={soft_pass_total}",
        f"rejected_total={rejected_total}",
        f"sources_total={sources_total}",
        f"success_count={success_count}",
        f"fail_count={fail_count}",
        f"event_candidates={len(event_cards)}",
    ]
    _add_multiline_textbox(
        slide, Cm(3), Cm(3.5), Cm(28), Cm(10),
        lines, font_size=16, color=TEXT_WHITE, line_spacing=1.4,
    )

    top_density = list(m.get("density_score_top5", []))[:3]
    if top_density:
        preview = []
        for row in top_density:
            title = str(row[0]) if isinstance(row, (list, tuple)) and len(row) > 0 else "candidate"
            score = int(row[2]) if isinstance(row, (list, tuple)) and len(row) > 2 else 0
            preview.append(f"{safe_text(title, 30)} ({score})")
        _add_textbox(slide, Cm(3), Cm(14.5), Cm(28), Cm(2.5),
                     f"density_score_top5: {' | '.join(preview)}",
                     font_size=12, color=SUBTLE_GRAY)


def _slide_event_ranking(
    prs: Presentation,
    event_cards: list[EduNewsCard],
    cards: list[EduNewsCard] | None = None,
) -> None:
    """Event ranking slide; in no-event mode use signal/corp-backed ranking rows."""
    headers = ["Rank", "Impact", "標題", "類別", "Action"]
    rows = []
    if event_cards:
        scored = []
        for c in event_cards[:8]:
            impact = score_event_impact(c)
            scored.append((c, impact))
        scored.sort(key=lambda x: x[1]["impact"], reverse=True)

        for rank, (c, imp) in enumerate(scored, 1):
            dc = build_decision_card(c)
            action = dc["actions"][0] if dc["actions"] else "待確認"
            action = semantic_guard_text(safe_text(action, 25), c, context="action")
            rows.append([
                str(rank),
                f"{imp['impact']}/5 {imp['label']}",
                safe_text(c.title_plain, 25),
                c.category or "綜合",
                action[:25],
            ])
    else:
        signals = build_signal_summary(cards or [])
        for rank, sig in enumerate(signals[:3], 1):
            heat_score = int(sig.get("heat_score", 30) or 30)
            action = "WATCH" if rank <= 2 else "TEST"
            rows.append(
                [
                    str(rank),
                    f"{heat_score}/100",
                    safe_text(str(sig.get("title", sig.get("signal_text", "來源訊號"))), 25),
                    "No-Event",
                    action,
                ]
            )
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

    from utils.semantic_quality import is_placeholder_or_fragment as _is_frag_ppt
    for act in actions[:6]:
        tag_color = type_colors.get(act["action_type"], SUBTLE_GRAY)

        # Guard (D): replace fragment detail with safe fallback
        detail_text = act["detail"]
        if not detail_text or _is_frag_ppt(detail_text):
            detail_text = "持續監控此事件發展（T+7）"

        # Action type badge
        _add_textbox(slide, Cm(2), Cm(y), Cm(4), Cm(0.9),
                     act["action_type"], font_size=14, bold=True,
                     color=tag_color)
        # Title
        _add_textbox(slide, Cm(6.5), Cm(y), Cm(14), Cm(0.9),
                     act["title"], font_size=12, color=TEXT_WHITE)
        # Detail
        _add_textbox(slide, Cm(6.5), Cm(y + 0.8), Cm(20), Cm(0.7),
                     detail_text, font_size=10, color=SUBTLE_GRAY)
        # Owner
        _add_textbox(slide, Cm(27), Cm(y), Cm(5), Cm(0.9),
                     act["owner"], font_size=10, color=SUBTLE_GRAY,
                     alignment=PP_ALIGN.RIGHT)
        y += 2.0


def _slide_pending_decisions(prs: Presentation, event_cards: list[EduNewsCard]) -> None:
    """Last slide: pending decisions & owners."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)
    _add_textbox(slide, Cm(2), Cm(1.5), Cm(30), Cm(2.5),
                 "待決事項與 Owner", font_size=36, bold=True,
                 color=HIGHLIGHT_YELLOW)
    _add_divider(slide, Cm(2), Cm(3.8), Cm(4), color=ACCENT)

    items: list[str] = []
    for i, c in enumerate(event_cards[:5], 1):
        dc = build_decision_card(c)
        action = dc["actions"][0] if dc["actions"] else "待確認"
        # Semantic guard: ensure action is not hollow
        action = semantic_guard_text(safe_text(action, 55), c, context="action")
        owner = dc["owner"]
        impact_data = score_event_impact(c)
        impact = impact_data.get("impact", 3) if isinstance(impact_data, dict) else 3
        why_raw = safe_text(c.why_important or "", 40) or safe_text(c.title_plain or "", 30) or ""
        from utils.semantic_quality import is_placeholder_or_fragment as _is_frag
        why_snippet = "" if _is_frag(why_raw) else why_raw
        items.append(
            f"{i}. {action[:55]} → Owner: {owner} "
            f"| Due: T+7 | Metric: impact={impact}/5"
            + (f" | {why_snippet}" if why_snippet else "")
        )

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
    _slide_structured_summary(prs, cards, metrics=metrics)

    # 3. Signal Thermometer (v5)
    _slide_signal_thermometer(prs, cards)

    # 4. Corp Watch (v5)
    _slide_corp_watch(prs, cards, metrics=metrics)

    # Event pool: only verifiable event cards (no synthetic placeholders).
    event_cards = get_event_cards_for_deck(cards, metrics=metrics or {}, min_events=0)

    # Quality guard: ensure per-card text density meets thresholds.
    for ec in event_cards:
        for attr in ("what_happened", "why_important"):
            val = getattr(ec, attr, None)
            if val:
                guarded, _m = quality_guard_block(val, card=ec)
                if guarded and guarded != val:
                    object.__setattr__(ec, attr, guarded)

    # 5. Key Takeaways
    _slide_key_takeaways(prs, cards, total_items, metrics=metrics, event_cards=event_cards)

    # 6. Overview Table
    _slide_overview_table(prs, event_cards, cards=cards)

    # 7. Event Ranking (v5)
    _slide_event_ranking(prs, event_cards, cards=cards)

    # 8. Per-event: brief_page1 + brief_page2
    for i, card in enumerate(event_cards, 1):
        _slide_brief_page1(prs, card, i)
        _slide_brief_page2(prs, card, i)

    # 9. Recommended Moves (v5)
    _slide_recommended_moves(prs, cards)

    # 10. Decision Matrix (6 columns)
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
    _slide_pending_decisions(prs, event_cards)

    prs.save(str(output_path))
    log.info("Executive PPTX generated: %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# v5.2.2 overrides (append-only quality hotfix layer)
# ---------------------------------------------------------------------------

_v521_slide_structured_summary = _slide_structured_summary
_v521_slide_key_takeaways = _slide_key_takeaways


def _slide_structured_summary(
    prs: Presentation,
    cards: list[EduNewsCard],
    metrics: dict | None = None,
) -> None:
    """Structured summary with metric-backed fallback in no-event days."""
    summary = build_structured_executive_summary(cards, metrics=metrics or {})
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

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
        _add_textbox(slide, Cm(2), Cm(y), Cm(30), Cm(1),
                     sec_title, font_size=14, bold=True,
                     color=HIGHLIGHT_YELLOW)
        y += 1.0
        for item in items[:2]:
            _add_textbox(slide, Cm(3), Cm(y), Cm(28), Cm(0.8),
                         f"- {safe_text(item, 80)}", font_size=11,
                         color=TEXT_WHITE)
            y += 0.8
        y += 0.3


def _slide_key_takeaways(
    prs: Presentation,
    cards: list[EduNewsCard],
    total_items: int,
    metrics: dict | None = None,
    event_cards: list[EduNewsCard] | None = None,
) -> None:
    """Key takeaways with stats-backed no-event fallback."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)
    _add_textbox(slide, Cm(2), Cm(1.2), Cm(30), Cm(2),
                 "Key Takeaways", font_size=36, bold=True,
                 color=HIGHLIGHT_YELLOW)
    _add_divider(slide, Cm(2), Cm(3.2), Cm(4), color=ACCENT)

    if event_cards is None:
        event_cards = get_event_cards_for_deck(cards, metrics=metrics or {}, min_events=0)
    takeaways: list[str] = [f"本次掃描總量：{total_items}；事件候選：{len(event_cards)}。"]

    for c in event_cards[:3]:
        dc = build_decision_card(c)
        if bool(getattr(c, "low_confidence", False)):
            takeaways.append(f"低信心候選：{safe_text(c.title_plain, 35)}；重點：{dc['event']}。")
        else:
            takeaways.append(f"{safe_text(c.title_plain, 35)}；重點：{dc['event']}。")

    if not event_cards:
        fetched_total = int((metrics or {}).get("fetched_total", total_items))
        gate_pass_total = int((metrics or {}).get("gate_pass_total", sum(1 for c in cards if c.is_valid_news)))
        sources_total = int((metrics or {}).get("sources_total", 0))
        after_filter_total = int((metrics or {}).get("after_filter_total", gate_pass_total))
        takeaways.extend(
            [
                f"掃描概況：fetched_total={fetched_total}、gate_pass_total={gate_pass_total}。",
                f"來源覆蓋：sources_total={sources_total}、after_filter_total={after_filter_total}。",
                "今日未形成高信心事件，先以來源級統計維持決策資訊量。",
            ]
        )

    _add_multiline_textbox(
        slide, Cm(3), Cm(4.5), Cm(28), Cm(13),
        takeaways, font_size=18, color=TEXT_WHITE, line_spacing=1.8,
    )


# ===========================================================================
# EXEC_VISUAL_TEMPLATE_V1 — append-only visual layout override layer
# Implements T1-T6 template codes for 6 key slide types.
# All colours / sizes reference utils/exec_visual_tokens.py.
# Does NOT modify schemas/education_models.py.
# Does NOT add new pip dependencies.
# ===========================================================================

import json as _json

from utils.exec_visual_tokens import (
    PRIMARY_BLUE as _V1_BLUE,
    SOFT_BLUE_BG as _V1_SOFT_BG,
    TEXT_GRAY as _V1_TEXT_GRAY,
    CARD_WHITE as _V1_CARD_WHITE,
    DIVIDER_GRAY as _V1_DIVIDER,
    GREEN_ACCENT as _V1_GREEN,
    ORANGE_ACCENT as _V1_AMBER,
    RED_ACCENT as _V1_RED,
    STAGE_COLORS as _V1_STAGE_COLORS,
    TITLE_FONT_SIZE as _V1_TITLE_FS,
    BODY_FONT_SIZE as _V1_BODY_FS,
    CARD_TITLE_FONT_SIZE as _V1_CARD_TITLE_FS,
    CARD_BODY_FONT_SIZE as _V1_CARD_BODY_FS,
    LABEL_FONT_SIZE as _V1_LABEL_FS,
    LINE_SPACING as _V1_LINE_SPACING,
    CARD_SHAPE_TYPE as _V1_ROUNDED_RECT,
    RECT_SHAPE_TYPE as _V1_RECT,
    LAYOUT_VERSION as _V1_LAYOUT_VER,
    TEMPLATE_MAP as _V1_TEMPLATE_MAP,
)
from utils.narrative_compact import (
    build_narrative_compact as _v1_narrative,
    has_hard_evidence as _v1_has_evidence,
    extract_first_hard_evidence as _v1_extract_ev,
)
from utils.bullet_normalizer import (
    normalize_bullets as _v1_norm_bullets,
    normalize_bullets_safe as _v1_norm_bullets_safe,
    compute_bullet_stats as _v1_bullet_stats,
)


# ---------------------------------------------------------------------------
# V1 Shape / Text helpers
# ---------------------------------------------------------------------------

def _v1_set_shape_fill(shape, bg_color, line_color=None) -> None:
    """Set shape fill; remove border unless line_color specified."""
    shape.fill.solid()
    shape.fill.fore_color.rgb = bg_color
    if line_color is None:
        shape.line.fill.background()
    else:
        shape.line.fill.solid()
        shape.line.fill.fore_color.rgb = line_color


def _v1_add_card(
    slide,
    left_cm: float, top_cm: float,
    width_cm: float, height_cm: float,
    header_text: str = '',
    body_lines: list[str] | None = None,
    bg_color=None,
    header_color=None,
    body_color=None,
    header_font_size: int = 12,
    body_font_size: int = 10,
    shape_type: int = 1,
) -> None:
    """Draw a card (filled rectangle) with optional header + body lines."""
    if bg_color is None:
        bg_color = _V1_SOFT_BG
    if header_color is None:
        header_color = _V1_BLUE
    if body_color is None:
        body_color = _V1_TEXT_GRAY

    # Card background
    card = slide.shapes.add_shape(
        shape_type,
        Cm(left_cm), Cm(top_cm), Cm(width_cm), Cm(height_cm),
    )
    _v1_set_shape_fill(card, bg_color)

    # Header text
    if header_text:
        _add_textbox(
            slide,
            Cm(left_cm + 0.3), Cm(top_cm + 0.15),
            Cm(width_cm - 0.4), Cm(0.8),
            safe_text(header_text, 60),
            font_size=header_font_size,
            color=header_color,
            bold=True,
        )

    # Body lines
    if body_lines:
        clean = [safe_text(b, 120) for b in body_lines if b.strip()]
        if clean:
            _add_multiline_textbox(
                slide,
                Cm(left_cm + 0.3), Cm(top_cm + 1.0),
                Cm(width_cm - 0.5), Cm(height_cm - 1.2),
                clean,
                font_size=body_font_size,
                color=body_color,
                line_spacing=_V1_LINE_SPACING,
            )


def _v1_slide_header(slide, title: str, report_time: str = '') -> None:
    """Standard V1 slide header: large title left + date right + divider."""
    _add_textbox(
        slide, Cm(2.0), Cm(0.8), Cm(24), Cm(1.4),
        safe_text(title, 60),
        font_size=_V1_TITLE_FS, bold=True, color=TEXT_WHITE,
    )
    if report_time:
        _add_textbox(
            slide, Cm(26), Cm(0.8), Cm(6.5), Cm(1.0),
            safe_text(report_time, 30),
            font_size=_V1_LABEL_FS, color=SUBTLE_GRAY,
            alignment=PP_ALIGN.RIGHT,
        )
    # Accent divider under header
    _add_divider(slide, Cm(2.0), Cm(2.4), Cm(29.5), color=_V1_BLUE)


def _v1_mini_arrow(slide, x_cm: float, y_cm: float) -> None:
    """Small horizontal arrow connector between cards."""
    arrow = slide.shapes.add_shape(_V1_RECT, Cm(x_cm), Cm(y_cm), Cm(0.8), Cm(0.15))
    _v1_set_shape_fill(arrow, _V1_BLUE)


def _v1_vertical_connector(slide, x_cm: float, y_top: float, y_bot: float) -> None:
    """Thin vertical line connecting T1 cards."""
    h = max(y_bot - y_top, 0.1)
    line = slide.shapes.add_shape(_V1_RECT, Cm(x_cm), Cm(y_top), Cm(0.12), Cm(h))
    _v1_set_shape_fill(line, _V1_BLUE)


# ---------------------------------------------------------------------------
# Save references to v5.2.2 versions before overriding
# ---------------------------------------------------------------------------
_v1_prev_slide_overview_table = _slide_overview_table
_v1_prev_slide_signal_thermometer = _slide_signal_thermometer
_v1_prev_slide_event_ranking = _slide_event_ranking
_v1_prev_slide_pending_decisions = _slide_pending_decisions
_v1_prev_slide_brief_page1 = _slide_brief_page1
_v1_prev_slide_brief_page2 = _slide_brief_page2
_v1_prev_generate_executive_ppt = generate_executive_ppt


# ---------------------------------------------------------------------------
# T5: Horizontal Rail — 今日總覽 Overview
# ---------------------------------------------------------------------------

def _slide_overview_table(
    prs: Presentation,
    event_cards: list[EduNewsCard],
    cards: list[EduNewsCard] | None = None,
) -> None:
    """T5: Horizontal Rail layout for 今日總覽 Overview slide."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)
    _v1_slide_header(slide, '今日總覽  Overview')

    # Source data for nodes
    if event_cards:
        items = event_cards[:8]
        node_data = []
        for c in items:
            title_short = safe_text(c.title_plain or '', 30)
            keypoint = safe_text(c.what_happened or '', 45) or safe_text(c.why_important or '', 45)
            if not keypoint:
                keypoint = safe_text(c.title_plain or '', 45)
            score_str = f'{c.final_score:.1f}' if getattr(c, 'final_score', None) is not None else ''
            node_data.append({'title': title_short, 'key': keypoint, 'cat': c.category or '綜合', 'score': score_str})
    else:
        sigs = build_signal_summary(cards or [])
        node_data = []
        for sig in sigs[:8]:
            t = safe_text(str(sig.get('title', sig.get('signal_text', '訊號'))), 14)
            k = safe_text(str(sig.get('label', sig.get('signal_type', ''))), 20)
            node_data.append({'title': t, 'key': k, 'cat': 'Signal'})

    display = node_data[:8] if node_data else [{'title': '今日無事件', 'key': '持續監控中', 'cat': '綜合', 'score': ''}]
    n = len(display)

    # Left icon block
    icon = slide.shapes.add_shape(_V1_RECT, Cm(1.5), Cm(3.0), Cm(2.2), Cm(3.5))
    _v1_set_shape_fill(icon, _V1_BLUE)
    _add_textbox(
        slide, Cm(1.55), Cm(3.8), Cm(2.1), Cm(1.5),
        'TODAY', font_size=13, bold=True,
        color=_V1_CARD_WHITE, alignment=PP_ALIGN.CENTER,
    )

    # Horizontal rail
    rail_x_start, rail_x_end = 4.0, 32.5
    rail_y = 4.8
    rail = slide.shapes.add_shape(
        _V1_RECT,
        Cm(rail_x_start), Cm(rail_y), Cm(rail_x_end - rail_x_start), Cm(0.22),
    )
    _v1_set_shape_fill(rail, _V1_BLUE)

    # Nodes + cards
    rail_span = rail_x_end - rail_x_start
    step = rail_span / max(n, 1)
    for i, item in enumerate(display):
        nx = rail_x_start + (i + 0.5) * step
        # Node marker
        nmarker = slide.shapes.add_shape(
            _V1_RECT, Cm(nx - 0.3), Cm(rail_y - 0.25), Cm(0.6), Cm(0.6),
        )
        _v1_set_shape_fill(nmarker, _V1_BLUE)

        # Card below rail
        card_left = max(nx - 2.1, rail_x_start - 0.5)
        card_left = min(card_left, rail_x_end - 4.0)
        card_w = 4.0
        card_y = rail_y + 0.6
        bg = _V1_SOFT_BG if i % 2 == 0 else _V1_CARD_WHITE
        score_line = f'Score: {item["score"]}' if item.get('score') else f'[{item["cat"]}]'
        _v1_add_card(
            slide, card_left, card_y, card_w, 3.8,
            header_text=item['title'],
            body_lines=[item['key'], score_line],
            bg_color=bg,
            header_color=_V1_BLUE,
            body_color=_V1_TEXT_GRAY,
            header_font_size=_V1_CARD_TITLE_FS,
            body_font_size=_V1_CARD_BODY_FS,
        )

    # Overflow note
    if event_cards and len(event_cards) > 8:
        _add_textbox(
            slide, Cm(2.0), Cm(17.8), Cm(29), Cm(0.7),
            f'另有 {len(event_cards) - 8} 則事件見 Event Ranking',
            font_size=_V1_LABEL_FS, color=SUBTLE_GRAY,
        )


# ---------------------------------------------------------------------------
# T2: Stage Arrow — Signal Thermometer
# ---------------------------------------------------------------------------

def _slide_signal_thermometer(prs: Presentation, cards: list[EduNewsCard]) -> None:
    """T2: Stage Arrow layout for Signal Thermometer slide."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    heat = compute_market_heat(cards)
    collected_at = ''
    for c in cards[:1]:
        collected_at = safe_text(getattr(c, 'source_name', '') or '', 20)

    _v1_slide_header(
        slide,
        f'Signal Thermometer  |  Market Heat Index: {heat["score"]} / 100',
        report_time=collected_at or heat.get('level', ''),
    )

    signals = build_signal_summary(cards)
    stage_names = ['Market Heat', 'Model Release', 'Productization', 'Regulatory / GTM']
    stage_narratives: list[str] = []
    heat_map = {
        'VERY_HIGH': '市場熱度極高；多方訊號密集匯聚。',
        'HIGH': '訊號強烈，需立即關注相關動態。',
        'MEDIUM': '訊號持續累積，保持追蹤。',
        'LOW': '目前訊號平穩，定期監控即可。',
    }
    for i in range(4):
        if i < len(signals):
            sig = signals[i]
            s_title = str(sig.get('title', sig.get('signal_text', '')))
            s_label = str(sig.get('label', sig.get('signal_type', '')))
            platform_count = int(sig.get('platform_count', sig.get('source_count', 0)))
            heat_word = str(sig.get('heat', 'cool')).upper()
            stage_narratives.append(
                f'{s_label}: {safe_text(s_title, 35)} [{heat_word}] (×{platform_count})'
            )
        else:
            if i == 0:
                stage_narratives.append(heat_map.get(heat.get('level', 'MEDIUM'), heat_map['MEDIUM']))
            else:
                stage_narratives.append('本週段無顯著新訊號；持續監測中。')

    # Draw 4 stage cards with arrows
    card_w, card_h = 7.0, 8.0
    positions_x = [1.3, 9.3, 17.3, 25.3]
    card_top = 3.2
    for i, (stage_name, narrative) in enumerate(zip(stage_names, stage_narratives)):
        color = _V1_STAGE_COLORS[i]
        # Stage label bar
        label_bar = slide.shapes.add_shape(
            _V1_RECT,
            Cm(positions_x[i]), Cm(card_top), Cm(card_w), Cm(1.2),
        )
        _v1_set_shape_fill(label_bar, color)
        _add_textbox(
            slide,
            Cm(positions_x[i] + 0.2), Cm(card_top + 0.1),
            Cm(card_w - 0.3), Cm(1.0),
            stage_name, font_size=11, bold=True, color=_V1_CARD_WHITE,
        )
        # Card body
        body_card = slide.shapes.add_shape(
            _V1_ROUNDED_RECT,
            Cm(positions_x[i]), Cm(card_top + 1.2),
            Cm(card_w), Cm(card_h - 1.2),
        )
        _v1_set_shape_fill(body_card, _V1_SOFT_BG)
        _add_textbox(
            slide,
            Cm(positions_x[i] + 0.3), Cm(card_top + 1.5),
            Cm(card_w - 0.5), Cm(card_h - 1.8),
            safe_text(narrative, 120),
            font_size=_V1_CARD_BODY_FS,
            color=_V1_TEXT_GRAY,
        )
        # Arrow connector
        if i < 3:
            arr_x = positions_x[i] + card_w + 0.05
            arr_y = card_top + card_h / 2
            _v1_mini_arrow(slide, arr_x, arr_y)
            _add_textbox(
                slide,
                Cm(arr_x + 0.1), Cm(arr_y - 0.35), Cm(0.7), Cm(0.7),
                '→', font_size=14, bold=True, color=_V1_BLUE,
            )

    # Bottom summary
    _add_textbox(
        slide, Cm(2.0), Cm(17.5), Cm(29), Cm(0.8),
        f'Market Heat Level: {heat["level"]}  |  Trend: {heat["trend_word"]}  |  Score: {heat["score"]}/100',
        font_size=_V1_LABEL_FS, color=SUBTLE_GRAY,
    )


# ---------------------------------------------------------------------------
# T4: 3-Column Bucket Cards — Event Ranking
# ---------------------------------------------------------------------------

def _slide_event_ranking(
    prs: Presentation,
    event_cards: list[EduNewsCard],
    cards: list[EduNewsCard] | None = None,
) -> None:
    """T4: Three-column bucket cards for Event Ranking 事件影響力排行."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)
    _v1_slide_header(slide, 'Event Ranking  事件影響力排行')

    # Bucket events by category
    buckets: dict[str, list[EduNewsCard]] = {'Product': [], 'Tech': [], 'Business': []}
    if event_cards:
        assigned: set[str] = set()
        for c in event_cards[:9]:
            cat = (c.category or '').lower()
            if any(k in cat for k in ('product', '產品', 'saas', 'app', '應用')):
                buckets['Product'].append(c)
                assigned.add(c.item_id)
            elif any(k in cat for k in ('tech', '技術', 'ai', '模型', 'model', 'gpu')):
                buckets['Tech'].append(c)
                assigned.add(c.item_id)
            elif any(k in cat for k in ('business', '商業', '市場', '融資', 'market', 'fund')):
                buckets['Business'].append(c)
                assigned.add(c.item_id)
        # Distribute unassigned events evenly
        for i, c in enumerate(ec for ec in event_cards[:9] if ec.item_id not in assigned):
            key = ['Product', 'Tech', 'Business'][i % 3]
            buckets[key].append(c)

    col_configs = [
        ('Product', _V1_BLUE, 1.3),
        ('Tech', _V1_GREEN, 11.8),
        ('Business', _V1_AMBER, 22.3),
    ]
    col_w = 9.5
    header_h = 1.2
    card_top_start = 5.3
    card_h = 4.6
    card_gap = 0.3

    for col_name, col_color, col_x in col_configs:
        # Column header bar
        hdr = slide.shapes.add_shape(
            _V1_RECT, Cm(col_x), Cm(3.3), Cm(col_w), Cm(header_h),
        )
        _v1_set_shape_fill(hdr, col_color)
        _add_textbox(
            slide, Cm(col_x + 0.3), Cm(3.4), Cm(col_w - 0.4), Cm(1.0),
            col_name, font_size=14, bold=True, color=_V1_CARD_WHITE,
        )
        uline = slide.shapes.add_shape(
            _V1_RECT, Cm(col_x), Cm(3.3 + header_h), Cm(col_w), Cm(0.1),
        )
        _v1_set_shape_fill(uline, col_color)

        col_events = buckets.get(col_name, [])[:2]

        for j in range(2):
            cy = card_top_start + j * (card_h + card_gap)
            if j < len(col_events):
                c = col_events[j]
                impact_data = score_event_impact(c)
                impact_score = impact_data.get('impact', 3) if isinstance(impact_data, dict) else 3
                dc = build_decision_card(c)
                action = dc['actions'][0] if dc['actions'] else '持續追蹤'
                bullets = _v1_norm_bullets_safe(
                    [safe_text(c.what_happened or '', 50), safe_text(action, 40)],
                )
                _v1_add_card(
                    slide, col_x, cy, col_w, card_h,
                    header_text=safe_text(c.title_plain or '', 22),
                    body_lines=bullets[:2],
                    bg_color=_V1_SOFT_BG,
                    header_color=col_color,
                    body_color=_V1_TEXT_GRAY,
                    header_font_size=_V1_CARD_TITLE_FS,
                    body_font_size=_V1_CARD_BODY_FS,
                )
                _add_textbox(
                    slide, Cm(col_x + 0.3), Cm(cy + card_h - 0.7), Cm(col_w - 0.4), Cm(0.6),
                    f'Impact: {impact_score}/5',
                    font_size=9, color=SUBTLE_GRAY,
                )
            else:
                _v1_add_card(
                    slide, col_x, cy, col_w, card_h,
                    header_text='監控中',
                    body_lines=['本欄暫無事件；持續掃描來源中。'],
                    bg_color=_V1_CARD_WHITE,
                    header_color=SUBTLE_GRAY,
                    body_color=SUBTLE_GRAY,
                    header_font_size=_V1_CARD_TITLE_FS,
                    body_font_size=_V1_CARD_BODY_FS,
                )


# ---------------------------------------------------------------------------
# T6: Promotion Curve — 待決事項與 Owner
# ---------------------------------------------------------------------------

def _slide_pending_decisions(prs: Presentation, event_cards: list[EduNewsCard]) -> None:
    """T6: Promotion Curve layout for 待決事項與 Owner slide."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)
    _v1_slide_header(slide, '待決事項與 Owner')

    # Horizontal guide rail
    guide = slide.shapes.add_shape(
        _V1_RECT, Cm(2.0), Cm(6.8), Cm(24.0), Cm(0.18),
    )
    _v1_set_shape_fill(guide, _V1_BLUE)

    milestone_labels = ['NOW', 'Next 7 Days', 'Next 30 Days']
    milestone_x = [2.5, 10.5, 18.5]
    milestone_w = 7.5
    ec_subset = event_cards[:3] if event_cards else []

    for i, (label, mx) in enumerate(zip(milestone_labels, milestone_x)):
        # Milestone node
        node = slide.shapes.add_shape(
            _V1_ROUNDED_RECT, Cm(mx + 3.0), Cm(6.0), Cm(1.4), Cm(1.4),
        )
        _v1_set_shape_fill(node, _V1_BLUE)
        _add_textbox(
            slide, Cm(mx + 3.0), Cm(6.1), Cm(1.4), Cm(1.2),
            str(i + 1), font_size=14, bold=True,
            color=_V1_CARD_WHITE, alignment=PP_ALIGN.CENTER,
        )
        # Label
        _add_textbox(
            slide, Cm(mx + 0.2), Cm(4.8), Cm(milestone_w), Cm(0.9),
            label, font_size=12, bold=True, color=TEXT_WHITE,
        )
        # Owner + bullets
        if i < len(ec_subset):
            c = ec_subset[i]
            dc = build_decision_card(c)
            owner = dc.get('owner', 'CXO')
            action = dc['actions'][0] if dc['actions'] else '持續追蹤'
            why_short = safe_text(c.why_important or '', 45) or safe_text(c.title_plain or '', 45)
            bullets = _v1_norm_bullets_safe([safe_text(action, 45), why_short])
            score_val = getattr(c, 'final_score', None)
            score_line = f'Score: {score_val:.1f}/10' if score_val is not None else ''
        else:
            owner = 'CXO'
            bullets = ['持續監控市場動態（T+7）。', '評估後續行動是否必要。']
            score_line = ''

        bullets = bullets[:2]
        body_lines = [f'Owner: {owner}'] + bullets
        if score_line:
            body_lines.append(score_line)
        _v1_add_card(
            slide, mx + 0.2, 7.8, milestone_w, 7.5,
            header_text='',
            body_lines=body_lines,
            bg_color=_V1_SOFT_BG,
            header_color=_V1_BLUE,
            body_color=_V1_TEXT_GRAY,
            body_font_size=_V1_CARD_BODY_FS,
        )

    # Right-side risks block
    _add_textbox(
        slide, Cm(26.5), Cm(4.8), Cm(6.5), Cm(1.0),
        'Top Risks / Watch', font_size=12, bold=True, color=TEXT_WHITE,
    )
    risks: list[str] = []
    for c in event_cards[:3]:
        dc = build_decision_card(c)
        if dc.get('risks'):
            risks.append(safe_text(dc['risks'][0], 40))
    if not risks:
        risks = ['監控市場波動', '跟蹤競爭對手動作']
    risks = _v1_norm_bullets_safe(risks[:2])
    _add_multiline_textbox(
        slide, Cm(26.5), Cm(6.0), Cm(6.5), Cm(5.0),
        [f'• {r}' for r in risks],
        font_size=10, color=SUBTLE_GRAY, line_spacing=1.5,
    )
    _add_divider(slide, Cm(0), Cm(18.7), SLIDE_WIDTH, color=ACCENT)


# ---------------------------------------------------------------------------
# T1: Three-card Curved Timeline — Event Slide A: What / Why / Proof
# ---------------------------------------------------------------------------

def _slide_brief_page1(prs: Presentation, card: EduNewsCard, idx: int) -> None:
    """T1: Three-card Curved Timeline layout for WHAT HAPPENED slide."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    # Event badge + title
    _add_textbox(
        slide, Cm(1.5), Cm(0.5), Cm(3.0), Cm(1.2),
        f'#{idx}', font_size=28, bold=True, color=ACCENT,
    )
    _add_textbox(
        slide, Cm(4.5), Cm(0.5), Cm(26.0), Cm(1.2),
        safe_text(card.title_plain or '', 55),
        font_size=22, bold=True, color=TEXT_WHITE,
    )
    _v1_slide_header(slide, 'WHAT HAPPENED')

    # Hero image (optional, right side)
    img_right = False
    try:
        img_path = get_news_image(card.title_plain, card.category)
        if img_path and img_path.exists():
            slide.shapes.add_picture(
                str(img_path), Cm(23.5), Cm(2.8), Cm(10.0), Cm(6.0),
            )
            img_right = True
    except Exception:
        pass

    card_w = 20.5 if img_right else 30.0
    card_left = 1.8

    # Build narrative compact for Card 1 body
    narrative = _v1_narrative(card)
    brief = build_ceo_brief_blocks(card)
    trend_liner = brief.get('ai_trend_liner', '')

    # Card 1: Q1 — What Happened
    card1_top, card1_h = 2.8, 4.6
    _v1_add_card(
        slide, card_left, card1_top, card_w, card1_h,
        header_text='Q1 — What Happened',
        body_lines=[safe_text(narrative, 200)] + ([safe_text(trend_liner, 80)] if trend_liner else []),
        bg_color=_V1_SOFT_BG,
        header_color=_V1_BLUE,
        body_color=_V1_TEXT_GRAY,
        header_font_size=_V1_CARD_TITLE_FS,
        body_font_size=_V1_CARD_BODY_FS,
        shape_type=_V1_ROUNDED_RECT,
    )
    _v1_vertical_connector(slide, card_left + card_w / 2, card1_top + card1_h, card1_top + card1_h + 0.5)

    # Card 2: Q2 — Why It Matters
    card2_top = card1_top + card1_h + 0.6
    card2_h = 3.5
    why_text = safe_text(card.why_important or '', 150) or safe_text(brief.get('q1_meaning', ''), 150)
    q2_text = safe_text(brief.get('q2_impact', ''), 100)
    _v1_add_card(
        slide, card_left, card2_top, card_w, card2_h,
        header_text='Q2 — Why It Matters (Business / Tech / Product)',
        body_lines=[why_text, q2_text] if q2_text else [why_text],
        bg_color=_V1_SOFT_BG,
        header_color=_V1_BLUE,
        body_color=_V1_TEXT_GRAY,
        header_font_size=_V1_CARD_TITLE_FS,
        body_font_size=_V1_CARD_BODY_FS,
        shape_type=_V1_ROUNDED_RECT,
    )
    _v1_vertical_connector(slide, card_left + card_w / 2, card2_top + card2_h, card2_top + card2_h + 0.5)

    # Card 3: Proof
    card3_top = card2_top + card2_h + 0.6
    card3_h = 3.0
    all_text = ' '.join(filter(None, [
        card.title_plain or '',
        card.what_happened or '',
        ' '.join(getattr(card, 'fact_check_confirmed', []) or []),
        ' '.join(getattr(card, 'evidence_lines', []) or []),
        getattr(card, 'technical_interpretation', '') or '',
    ]))
    proof_token = _v1_extract_ev(all_text)
    source_label = safe_text(getattr(card, 'source_name', '') or '', 30)
    source_url = safe_text(getattr(card, 'source_url', '') or '', 60)
    proof_text = proof_token if proof_token else '詳見原始來源'
    proof_lines = [f'關鍵數據：{proof_text}']
    if source_label:
        proof_lines.append(f'來源：{source_label}')
    if source_url and source_url.startswith('http'):
        proof_lines.append(safe_text(source_url, 55))
    _v1_add_card(
        slide, card_left, card3_top, card_w, card3_h,
        header_text='Proof — Hard Evidence',
        body_lines=proof_lines,
        bg_color=_V1_SOFT_BG,
        header_color=_V1_GREEN,
        body_color=_V1_TEXT_GRAY,
        header_font_size=_V1_CARD_TITLE_FS,
        body_font_size=_V1_CARD_BODY_FS,
        shape_type=_V1_ROUNDED_RECT,
    )


# ---------------------------------------------------------------------------
# T3: Growth Steps — Event Slide B: Moves / Risks / Owner
# ---------------------------------------------------------------------------

def _slide_brief_page2(prs: Presentation, card: EduNewsCard, idx: int) -> None:
    """T3: Growth Steps staircase for WHY IT MATTERS — Action Plan slide."""
    brief = build_ceo_brief_blocks(card)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    # Header — preserve "WHY IT MATTERS" for existing-test compatibility
    _add_textbox(
        slide, Cm(2), Cm(0.5), Cm(28), Cm(1.2),
        f'#{idx}  WHY IT MATTERS — Action Plan',
        font_size=22, bold=True, color=HIGHLIGHT_YELLOW,
    )
    _add_divider(slide, Cm(2), Cm(1.8), Cm(4), color=ACCENT)

    # Q3 actions → staircase steps
    actions = brief.get('q3_actions', [])
    actions = _v1_norm_bullets_safe(
        [safe_text(a, 60) for a in (actions or [])],
    )
    while len(actions) < 3:
        actions.append('持續追蹤後續發展，確認關鍵指標後決定行動方向。')

    # Staircase layout
    step_configs = [
        (1.5, 13.0, 18.0, 3.5, _V1_AMBER, 'Q3-A  Move 1'),
        (5.5, 10.0, 14.5, 3.5, _V1_BLUE, 'Q3-B  Move 2'),
        (9.5, 7.0, 11.0, 3.5, _V1_GREEN, 'Q3-C  Move 3'),
    ]
    for step_i, (sx, sy, sw, sh, sc, slabel) in enumerate(step_configs):
        _v1_add_card(
            slide, sx, sy, sw, sh,
            header_text=f'{slabel}: {actions[step_i][:48]}',
            body_lines=[safe_text(actions[step_i], 80)],
            bg_color=_V1_SOFT_BG,
            header_color=sc,
            body_color=_V1_TEXT_GRAY,
            header_font_size=_V1_CARD_TITLE_FS,
            body_font_size=_V1_CARD_BODY_FS,
            shape_type=_V1_ROUNDED_RECT,
        )

    # Risks / Watch
    _add_divider(slide, Cm(2), Cm(15.0), Cm(29), color=SUBTLE_GRAY)
    _add_textbox(
        slide, Cm(2), Cm(15.2), Cm(18), Cm(0.8),
        'Risks / Watch', font_size=13, bold=True, color=TEXT_WHITE,
    )
    dc_card = build_decision_card(card)
    raw_risks = dc_card.get('risks', [])
    if not raw_risks:
        raw_risks = [safe_text(brief.get('q1_meaning', ''), 55) or '持續監控此事件後續影響。']
    risks = _v1_norm_bullets_safe(raw_risks[:2])
    for ri, rk in enumerate(risks[:2]):
        _add_textbox(
            slide, Cm(3), Cm(16.2 + ri * 0.9), Cm(18), Cm(0.8),
            f'• {safe_text(rk, 60)}', font_size=11, color=SUBTLE_GRAY,
        )

    # Owner / ETA
    owner = dc_card.get('owner', 'CXO')
    _add_textbox(
        slide, Cm(22), Cm(15.2), Cm(11), Cm(0.8),
        f'Owner: {owner}  |  ETA: T+7',
        font_size=12, bold=True, color=HIGHLIGHT_YELLOW,
    )

    # Video reference (preserve for test_video_reference_present)
    videos = brief.get('video_source', [])
    if videos:
        vid = videos[0]
        _add_textbox(
            slide, Cm(2), Cm(17.0), Cm(20), Cm(0.7),
            f'Video: {safe_text(vid.get("title", ""), 50)}',
            font_size=9, color=SUBTLE_GRAY,
        )

    # Sources
    sources = brief.get('sources', [])
    if sources:
        _add_textbox(
            slide, Cm(2), Cm(17.8), Cm(29), Cm(0.7),
            f'Source: {safe_text(sources[0], 80)}',
            font_size=9, color=SUBTLE_GRAY,
        )


# ---------------------------------------------------------------------------
# Meta JSON writer
# ---------------------------------------------------------------------------

def _v1_write_exec_layout_meta(
    output_path: 'Path',
    event_cards: list[EduNewsCard],
    all_cards: list[EduNewsCard],
) -> None:
    """Compute and write outputs/exec_layout.meta.json."""
    from datetime import datetime as _dt, timezone as _tz

    meta_path = output_path.parent / 'exec_layout.meta.json'

    # Build slide_layout_map from static deck structure
    n_events = len(event_cards)
    slide_layout_map: list[dict] = [
        {'slide_no': 1, 'template_code': 'COVER', 'title': 'CEO Decision Brief'},
        {'slide_no': 2, 'template_code': 'STRUCTURED_SUMMARY', 'title': 'Structured Summary'},
        {'slide_no': 3, 'template_code': 'T2', 'title': 'Signal Thermometer'},
        {'slide_no': 4, 'template_code': 'CORP_WATCH', 'title': 'Corp Watch'},
        {'slide_no': 5, 'template_code': 'KEY_TAKEAWAYS', 'title': 'Key Takeaways'},
        {'slide_no': 6, 'template_code': 'T5', 'title': '今日總覽 Overview'},
        {'slide_no': 7, 'template_code': 'T4', 'title': 'Event Ranking 事件影響力排行'},
    ]
    slide_cursor = 8
    for i in range(n_events):
        ev_title = (event_cards[i].title_plain or f'Event {i + 1}')[:30]
        slide_layout_map.append({
            'slide_no': slide_cursor,
            'template_code': 'T1',
            'title': f'#{i + 1} WHAT HAPPENED — {ev_title}',
        })
        slide_layout_map.append({
            'slide_no': slide_cursor + 1,
            'template_code': 'T3',
            'title': f'#{i + 1} WHY IT MATTERS — Action Plan',
        })
        slide_cursor += 2
    slide_layout_map.extend([
        {'slide_no': slide_cursor, 'template_code': 'REC_MOVES', 'title': 'Recommended Moves'},
        {'slide_no': slide_cursor + 1, 'template_code': 'DECISION_MATRIX', 'title': 'Decision Matrix'},
        {'slide_no': slide_cursor + 2, 'template_code': 'T6', 'title': '待決事項與 Owner'},
    ])

    # Fragment stats
    all_bullet_lists: list[list[str]] = []
    fragments_detected = 0
    fragments_fixed = 0

    for c in event_cards:
        raw_bullets: list[str] = []
        for attr in ('action_items', 'derivable_effects', 'fact_check_confirmed'):
            vals = getattr(c, attr, []) or []
            raw_bullets.extend(str(v) for v in vals)
        short = [b for b in raw_bullets if len(b.strip()) < 12 and b.strip()]
        fragments_detected += len(short)
        if short:
            normed = _v1_norm_bullets(raw_bullets)
            still_short = len([b for b in normed if len(b.strip()) < 12])
            fixed = max(0, len(short) - still_short)
            fragments_fixed += fixed
        all_bullet_lists.append(raw_bullets)

    total = max(fragments_detected, 1)
    fragment_ratio = round(fragments_fixed / total, 4)

    # Bullet length stats
    norm_bullet_lists = [_v1_norm_bullets(bl) for bl in all_bullet_lists]
    bstats = _v1_bullet_stats(norm_bullet_lists)

    # Proof token coverage
    proof_covered = 0
    for c in event_cards:
        combined = ' '.join(filter(None, [
            c.title_plain or '',
            c.what_happened or '',
            c.why_important or '',
            getattr(c, 'technical_interpretation', '') or '',
        ]))
        if _v1_has_evidence(combined):
            proof_covered += 1
    proof_ratio = round(proof_covered / max(len(event_cards), 1), 4)

    # Sentence count stats
    from utils.semantic_quality import count_sentences as _count_sents_v1
    sent_counts = []
    for c in event_cards:
        txt = (c.what_happened or '') + ' ' + (c.why_important or '')
        sent_counts.append(_count_sents_v1(txt))
    avg_sents = round(sum(sent_counts) / max(len(sent_counts), 1), 2)

    meta = {
        'layout_version': _V1_LAYOUT_VER,
        'generated_at': _dt.now(tz=_tz.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'template_map': _V1_TEMPLATE_MAP,
        'slide_layout_map': slide_layout_map,
        'fragment_fix_stats': {
            'fragments_detected': fragments_detected,
            'fragments_fixed': fragments_fixed,
            'fragment_ratio': fragment_ratio,
        },
        'bullet_len_stats': bstats,
        'card_stats': {
            'total_event_cards': len(event_cards),
            'avg_sentences_per_event_card': avg_sents,
            'proof_token_coverage_ratio': proof_ratio,
        },
    }

    try:
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            _json.dumps(meta, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        get_logger().info('exec_layout.meta.json written: %s', meta_path)
    except Exception as exc:
        get_logger().warning('Failed to write exec_layout.meta.json: %s', exc)

    # G4: update exec_quality.meta.json with fragment leak data
    try:
        import json as _json_q
        _q_path = meta_path.parent / 'exec_quality.meta.json'
        fragments_leaked = max(0, fragments_detected - fragments_fixed)
        fragment_leak_gate = 'PASS' if fragments_leaked == 0 else 'FAIL'
        if _q_path.exists():
            _qm = _json_q.loads(_q_path.read_text(encoding='utf-8'))
        else:
            _qm = {}
        _qm.update({
            'fragments_detected': fragments_detected,
            'fragments_fixed': fragments_fixed,
            'fragments_leaked': fragments_leaked,
            'fragment_leak_gate': fragment_leak_gate,
        })
        _q_path.write_text(
            _json_q.dumps(_qm, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
    except Exception as _exc_q:
        get_logger().warning('exec_quality.meta G4 update error (non-fatal): %s', _exc_q)


# ---------------------------------------------------------------------------
# Override generate_executive_ppt to write exec_layout.meta.json
# ---------------------------------------------------------------------------

def generate_executive_ppt(
    cards: list[EduNewsCard],
    health: 'SystemHealthReport',
    report_time: str,
    total_items: int,
    output_path: 'Path | None' = None,
    theme: str = 'light',
    metrics: dict | None = None,
) -> 'Path':
    """V1 wrapper: runs original generator then writes exec_layout.meta.json."""
    result = _v1_prev_generate_executive_ppt(
        cards, health, report_time, total_items,
        output_path=output_path, theme=theme, metrics=metrics,
    )
    ev_cards = get_event_cards_for_deck(cards, metrics=metrics or {}, min_events=0)
    try:
        _v1_write_exec_layout_meta(result, ev_cards, cards)
    except Exception as exc:
        get_logger().warning('exec_layout.meta write error (non-fatal): %s', exc)
    return result

