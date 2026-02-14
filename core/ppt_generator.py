"""PPTX 教育版簡報生成器。

商務風格：深藍背景 + 橘色重點 + 白色文字。
6 種 slide layout：Cover, Section, Contents, Text, Image+Text, Conclusion。
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Cm, Emu, Pt
from schemas.education_models import EduNewsCard, SystemHealthReport
from utils.logger import get_logger

# ---------------------------------------------------------------------------
# Theme colours
# ---------------------------------------------------------------------------
PRIMARY = RGBColor(33, 40, 56)       # 深藍
ACCENT = RGBColor(230, 90, 55)       # 橘
WHITE = RGBColor(255, 255, 255)
LIGHT_GRAY = RGBColor(200, 200, 200)
DARK_GRAY = RGBColor(80, 80, 80)

SLIDE_WIDTH = Cm(33.867)   # 16:9 default
SLIDE_HEIGHT = Cm(19.05)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_slide_bg(slide, color: RGBColor = PRIMARY) -> None:
    """Fill slide background with solid colour."""
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = color


def _add_textbox(
    slide,
    left: Cm,
    top: Cm,
    width: Cm,
    height: Cm,
    text: str,
    font_size: int = 18,
    color: RGBColor = WHITE,
    bold: bool = False,
    alignment: PP_ALIGN = PP_ALIGN.LEFT,
) -> None:
    """Add a simple single-paragraph textbox."""
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
    color: RGBColor = WHITE,
    bold_first: bool = False,
    line_spacing: float = 1.3,
) -> None:
    """Add a textbox with multiple paragraphs."""
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


def _add_accent_circle(slide, left, top, size, text: str) -> None:
    """Add an orange circle with centered number/text."""
    shape = slide.shapes.add_shape(
        9,  # MSO_SHAPE.OVAL
        left, top, size, size,
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = ACCENT
    shape.line.fill.background()
    tf = shape.text_frame
    tf.word_wrap = False
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(int(size / Emu(1) / 12700 * 0.45))
    p.font.color.rgb = WHITE
    p.font.bold = True
    p.alignment = PP_ALIGN.CENTER
    tf.paragraphs[0].alignment = PP_ALIGN.CENTER
    shape.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER


# ---------------------------------------------------------------------------
# Slide builders
# ---------------------------------------------------------------------------


def _slide_cover(prs: Presentation, report_time: str) -> None:
    """Slide 1 — Cover."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    _set_slide_bg(slide)

    # Accent bar at top
    bar = slide.shapes.add_shape(1, Cm(0), Cm(0), SLIDE_WIDTH, Cm(0.8))
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()

    _add_textbox(slide, Cm(4), Cm(5), Cm(26), Cm(4),
                 "AI 情報教育報告", font_size=44, bold=True, alignment=PP_ALIGN.CENTER)
    _add_textbox(slide, Cm(4), Cm(9.5), Cm(26), Cm(2),
                 "Daily Tech Intelligence", font_size=24, color=LIGHT_GRAY, alignment=PP_ALIGN.CENTER)
    _add_textbox(slide, Cm(4), Cm(14), Cm(26), Cm(1.5),
                 report_time, font_size=14, color=LIGHT_GRAY, alignment=PP_ALIGN.CENTER)

    # Bottom accent bar
    bar2 = slide.shapes.add_shape(1, Cm(0), Cm(18.25), SLIDE_WIDTH, Cm(0.8))
    bar2.fill.solid()
    bar2.fill.fore_color.rgb = ACCENT
    bar2.line.fill.background()


def _slide_contents(prs: Presentation, chapters: list[str]) -> None:
    """Slide 2 — Table of contents."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    _add_textbox(slide, Cm(2), Cm(1.5), Cm(30), Cm(2.5),
                 "CONTENTS", font_size=36, bold=True, alignment=PP_ALIGN.LEFT)

    # Accent line under title
    line = slide.shapes.add_shape(1, Cm(2), Cm(3.8), Cm(6), Cm(0.15))
    line.fill.solid()
    line.fill.fore_color.rgb = ACCENT
    line.line.fill.background()

    for i, ch in enumerate(chapters):
        y = Cm(5.5 + i * 2.8)
        _add_accent_circle(slide, Cm(2.5), y, Cm(1.8), str(i + 1))
        _add_textbox(slide, Cm(5), y + Cm(0.2), Cm(24), Cm(2),
                     ch, font_size=22, color=WHITE)


def _slide_section(prs: Presentation, title: str, subtitle: str = "") -> None:
    """Section divider slide."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    # Left accent bar
    bar = slide.shapes.add_shape(1, Cm(0), Cm(0), Cm(1.2), SLIDE_HEIGHT)
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()

    _add_textbox(slide, Cm(3), Cm(6), Cm(28), Cm(4),
                 title, font_size=36, bold=True, alignment=PP_ALIGN.LEFT)
    if subtitle:
        _add_textbox(slide, Cm(3), Cm(10.5), Cm(28), Cm(2),
                     subtitle, font_size=18, color=LIGHT_GRAY)


def _slide_text(prs: Presentation, title: str, body_lines: list[str]) -> None:
    """Text-heavy slide."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    _add_textbox(slide, Cm(2), Cm(1.2), Cm(30), Cm(2),
                 title, font_size=28, bold=True, color=ACCENT)

    # Accent line
    line = slide.shapes.add_shape(1, Cm(2), Cm(3), Cm(5), Cm(0.12))
    line.fill.solid()
    line.fill.fore_color.rgb = ACCENT
    line.line.fill.background()

    _add_multiline_textbox(slide, Cm(2), Cm(3.8), Cm(30), Cm(14),
                           body_lines, font_size=16, color=WHITE)


def _slide_conclusion(prs: Presentation, items: list[str]) -> None:
    """Final Next Steps slide."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    _add_textbox(slide, Cm(2), Cm(1.5), Cm(30), Cm(2.5),
                 "NEXT STEPS", font_size=36, bold=True, color=ACCENT, alignment=PP_ALIGN.LEFT)

    line = slide.shapes.add_shape(1, Cm(2), Cm(3.8), Cm(6), Cm(0.15))
    line.fill.solid()
    line.fill.fore_color.rgb = ACCENT
    line.line.fill.background()

    for i, item in enumerate(items):
        y = Cm(5 + i * 2.5)
        _add_accent_circle(slide, Cm(2.5), y, Cm(1.6), str(i + 1))
        _add_textbox(slide, Cm(5), y + Cm(0.15), Cm(26), Cm(2),
                     item, font_size=18, color=WHITE)

    # Bottom bar
    bar = slide.shapes.add_shape(1, Cm(0), Cm(18.25), SLIDE_WIDTH, Cm(0.8))
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()


# ---------------------------------------------------------------------------
# News card slides (2 slides per card)
# ---------------------------------------------------------------------------


def _slides_news_card(prs: Presentation, card: EduNewsCard, idx: int) -> None:
    """Generate 2 slides per valid news card (summary + QA/details)."""
    if not card.is_valid_news:
        # Invalid card gets 1 slide
        _slide_text(prs, f"#{idx} — 無效內容", [
            f"判定：{card.invalid_reason or '非新聞內容'}",
            "",
            f"原因：{card.invalid_cause or '抓取失敗'}",
            f"修復建議：{card.invalid_fix or '調整抓取策略'}",
        ])
        return

    # Slide A: title + plain explanation
    body_a = [
        f"發生了什麼：{card.what_happened[:120]}",
        "",
        f"為什麼重要：{card.why_important[:120]}",
        "",
        f"你要關注什麼：{card.focus_action[:120]}",
    ]
    if card.metaphor:
        body_a.extend(["", f"類比理解：{card.metaphor[:100]}"])

    _slide_text(prs, f"#{idx}  {card.title_plain[:40]}", body_a)

    # Slide B: QA + facts
    body_b: list[str] = []
    if card.fact_check_confirmed:
        body_b.append("事實核對：")
        for f in card.fact_check_confirmed[:3]:
            body_b.append(f"  ✅ {f[:70]}")
        body_b.append("")

    if card.action_items:
        body_b.append("可執行行動：")
        for a in card.action_items[:3]:
            body_b.append(f"  • {a[:70]}")

    if card.evidence_lines:
        body_b.append("")
        body_b.append("證據片段：")
        for e in card.evidence_lines[:2]:
            body_b.append(f"  {e[:80]}")

    _slide_text(prs, f"#{idx}（續）QA & 行動", body_b)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_education_ppt(
    cards: list[EduNewsCard],
    health: SystemHealthReport,
    report_time: str,
    total_items: int,
    output_path: Path | None = None,
) -> Path:
    """Generate a business-style PPTX education report.

    Returns the path to the generated .pptx file.
    """
    log = get_logger()
    if output_path is None:
        project_root = Path(__file__).resolve().parent.parent
        output_path = project_root / "outputs" / "education_report.pptx"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    prs = Presentation()
    prs.slide_width = SLIDE_WIDTH
    prs.slide_height = SLIDE_HEIGHT

    # --- Slide 1: Cover ---
    _slide_cover(prs, report_time)

    # --- Slide 2: Contents ---
    chapters = [
        "今日重點  Today's Highlights",
        "AI 新聞解析  News Deep Dive",
        "系統健康指標  System Metrics",
        "下一步學習  Next Steps",
    ]
    _slide_contents(prs, chapters)

    # --- Section 1: Today's Highlights ---
    valid_cards = [c for c in cards if c.is_valid_news]
    valid_count = len(valid_cards)
    invalid_count = len(cards) - valid_count

    _slide_section(prs, "01  今日重點", "Today's Highlights")
    summary_lines = [
        f"分析項目數：{total_items} 則",
        f"有效新聞：{valid_count} 則",
        f"無效內容：{invalid_count} 則",
        f"成功率：{health.success_rate:.0f}%",
        f"總執行時間：{health.total_runtime:.1f} 秒",
        "",
        f"健康狀態：{health.traffic_light_label}",
    ]
    if valid_cards:
        summary_lines.append("")
        summary_lines.append("主要新聞：")
        for c in valid_cards[:3]:
            summary_lines.append(f"  • {c.title_plain[:50]}")
    _slide_text(prs, "今日結論  Executive Summary", summary_lines)

    # --- Section 2: News Cards ---
    _slide_section(prs, "02  AI 新聞解析", "News Deep Dive")
    for i, card in enumerate(cards, 1):
        _slides_news_card(prs, card, i)

    # --- Section 3: System Metrics ---
    _slide_section(prs, "03  系統健康指標", "System Metrics")
    metrics_lines = [
        f"Enrich 成功率：{health.success_rate:.0f}%",
        f"P50 延遲：{health.p50_latency:.1f}s",
        f"P95 延遲：{health.p95_latency:.1f}s",
        f"雜訊清除：{health.entity_noise_removed} 個",
        "",
        f"健康度：{health.traffic_light_emoji} {health.traffic_light_label}",
    ]
    if health.fail_reasons:
        metrics_lines.append("")
        metrics_lines.append("失敗原因：")
        for reason, count in health.fail_reasons.items():
            metrics_lines.append(f"  {reason}：{count} 次")
    _slide_text(prs, "系統效能儀表板", metrics_lines)

    # --- Section 4: Next Steps ---
    next_items = [
        "挑一則最感興趣的新聞，用自己的話說給朋友聽",
        "完成至少一張新聞卡片裡的行動建議",
        "回顧過去幾期報告，找出重複出現的趨勢關鍵字",
        "檢查系統健康指標，必要時調整來源設定",
    ]
    _slide_conclusion(prs, next_items)

    prs.save(str(output_path))
    log.info("Education PPTX generated: %s", output_path)
    return output_path
