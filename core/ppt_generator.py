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
from pptx.util import Cm, Emu, Pt

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
TABLE_BORDER = RGBColor(230, 230, 230)     # subtle border
DIVIDER_GRAY = RGBColor(220, 220, 220)     # thin divider line

SLIDE_WIDTH = Cm(33.867)   # 16:9 default
SLIDE_HEIGHT = Cm(19.05)

# ---------------------------------------------------------------------------
# Banned words — sanitize all slide text
# ---------------------------------------------------------------------------
BANNED_WORDS = [
    "ai捕捉", "AI Intel", "Z1", "Z2", "Z3", "Z4", "Z5",
    "pipeline", "ETL", "verify_run", "ingestion", "ai_core",
]

# Responsibility mapping for "要問誰" column
_RESPONSIBILITY_MAP = {
    "綜合": "策略長/PM",
    "tech": "策略長/PM",
    "科技/技術": "研發/CTO",
    "人工智慧": "研發/CTO",
    "資安": "資安長",
    "政策/監管": "法務",
    "法規": "法務",
    "金融/財經": "財務長/CFO",
    "創業/投融資": "策略長/PM",
    "氣候/能源": "營運/COO",
    "併購/企業": "策略長/CEO",
    "消費電子": "產品/PM",
    "遊戲/娛樂": "產品/PM",
}


def _sanitize(text: str) -> str:
    """Remove banned words from text."""
    result = text
    for bw in BANNED_WORDS:
        result = result.replace(bw, "")
    return result


def _responsible_party(category: str) -> str:
    """Map category to responsible party."""
    return _RESPONSIBILITY_MAP.get(category or "", "策略長/PM")


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
    p.text = _sanitize(text)
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
        p.text = _sanitize(line)
        p.font.size = Pt(font_size)
        p.font.color.rgb = color
        if bold_first and i == 0:
            p.font.bold = True
        p.space_after = Pt(font_size * (line_spacing - 1))


def _add_divider(slide, left, top, width, color: RGBColor = ACCENT) -> None:
    """Add a thin horizontal divider line."""
    line = slide.shapes.add_shape(1, left, top, width, Cm(0.05))
    line.fill.solid()
    line.fill.fore_color.rgb = color
    line.line.fill.background()


def _add_table_slide(prs: Presentation, title: str,
                     headers: list[str], rows: list[list[str]]) -> None:
    """Add a Notion-style minimal table slide (white bg, light header, thin borders)."""
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

    # Header row — light gray background, dark text
    for ci, h in enumerate(headers):
        cell = tbl.cell(0, ci)
        cell.text = _sanitize(h)
        for p in cell.text_frame.paragraphs:
            p.font.size = Pt(10)
            p.font.bold = True
            p.font.color.rgb = DARK_TEXT
        cell.fill.solid()
        cell.fill.fore_color.rgb = TABLE_HEADER_BG

    # Data rows — white background
    for ri, row_data in enumerate(rows):
        for ci, val in enumerate(row_data):
            cell = tbl.cell(ri + 1, ci)
            cell.text = _sanitize(val)
            for p in cell.text_frame.paragraphs:
                p.font.size = Pt(9)
                p.font.color.rgb = DARK_TEXT
            cell.fill.solid()
            cell.fill.fore_color.rgb = WHITE


# ---------------------------------------------------------------------------
# Slide builders — Notion document-style clean white design
# ---------------------------------------------------------------------------


def _slide_cover(prs: Presentation, report_time: str) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    # Thin accent line at top (not heavy bar)
    _add_divider(slide, Cm(0), Cm(0.5), SLIDE_WIDTH, color=ACCENT)

    _add_textbox(slide, Cm(4), Cm(5.5), Cm(26), Cm(4),
                 "每日科技趨勢簡報", font_size=44, bold=True,
                 color=DARK_TEXT, alignment=PP_ALIGN.CENTER)
    _add_textbox(slide, Cm(4), Cm(10), Cm(26), Cm(2),
                 "Daily Tech Intelligence Briefing", font_size=20,
                 color=MID_GRAY, alignment=PP_ALIGN.CENTER)

    # Small orange dot divider
    _add_divider(slide, Cm(15.5), Cm(12.5), Cm(3), color=ACCENT)

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

    _add_divider(slide, Cm(2), Cm(3.2), Cm(4), color=ACCENT)

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
    """Section divider — large title, thin accent line (no heavy bar)."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    # Thin accent line on left (not heavy bar)
    _add_divider(slide, Cm(2), Cm(6), Cm(4), color=ACCENT)

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

    _add_divider(slide, Cm(2), Cm(3.2), Cm(4), color=ACCENT)

    _add_multiline_textbox(slide, Cm(2.5), Cm(4), Cm(29), Cm(14),
                           body_lines, font_size=15, color=DARK_TEXT)


def _slide_image_text(prs: Presentation, title: str,
                      body_lines: list[str], img_path: Path | None) -> None:
    """Slide with full-width banner image on top, text below."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    _add_textbox(slide, Cm(2), Cm(0.6), Cm(30), Cm(1.5),
                 title, font_size=22, bold=True, color=DARK_TEXT)

    # Full-width banner image at top
    if img_path and img_path.exists():
        try:
            slide.shapes.add_picture(
                str(img_path), Cm(1), Cm(2.2), Cm(31.8), Cm(7.5),
            )
        except Exception:
            pass

    # Text below the banner
    text_top = Cm(10.2)
    _add_multiline_textbox(slide, Cm(2), text_top, Cm(30), Cm(8.5),
                           body_lines, font_size=14, color=DARK_TEXT,
                           line_spacing=1.5)


def _slide_conclusion(prs: Presentation, items: list[str]) -> None:
    """Next steps — numbered list, clean."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide)

    _add_textbox(slide, Cm(2), Cm(1.5), Cm(30), Cm(2.5),
                 "Next Steps", font_size=36, bold=True, color=DARK_TEXT)

    _add_divider(slide, Cm(2), Cm(3.8), Cm(4), color=ACCENT)

    numbered_lines = [f"{i+1}.  {item}" for i, item in enumerate(items)]
    _add_multiline_textbox(slide, Cm(3), Cm(5), Cm(28), Cm(13),
                           numbered_lines, font_size=18, color=DARK_TEXT,
                           line_spacing=1.8)

    # Thin bottom accent line
    _add_divider(slide, Cm(0), Cm(18.7), SLIDE_WIDTH, color=ACCENT)


# ---------------------------------------------------------------------------
# Decision card helpers (6-column)
# ---------------------------------------------------------------------------


def _build_decision_body(card: EduNewsCard) -> list[str]:
    """Build the 6-column decision card body lines for a single news item."""
    lines: list[str] = []

    # 1) 事件一句話 (≤22 chars)
    event_line = card.what_happened[:22] if card.what_happened else "事件摘要待補充"
    lines.append(f"事件：{event_line}")
    lines.append("")

    # 2) 已知事實 (3 points)
    lines.append("已知事實：")
    facts = card.fact_check_confirmed[:3] if card.fact_check_confirmed else []
    if not facts and card.evidence_lines:
        facts = card.evidence_lines[:3]
    if facts:
        for f in facts:
            lines.append(f"  • {f[:50]}")
    else:
        lines.append("  • 目前資料缺口：尚無經確認的事實來源")
    lines.append("")

    # 3) 可能影響 (2-3 points)
    lines.append("可能影響：")
    effects = card.derivable_effects[:3] if card.derivable_effects else []
    if not effects:
        if card.why_important:
            effects = [card.why_important[:60]]
        else:
            effects = ["目前資料缺口：影響面待進一步分析"]
    for e in effects:
        lines.append(f"  • {e[:50]}")
    lines.append("")

    # 4) 主要風險 (2 points)
    lines.append("主要風險：")
    risks = card.speculative_effects[:2] if card.speculative_effects else []
    if not risks:
        risks = ["若事件擴大→相關業務可能受波及"]
    for r in risks:
        lines.append(f"  • {r[:50]}")

    return lines


# ---------------------------------------------------------------------------
# News card slides
# ---------------------------------------------------------------------------


def _slides_news_card(prs: Presentation, card: EduNewsCard, idx: int) -> None:
    """Generate slides per news card — one idea per slide, banner layout."""
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

    # Build 6-column decision card body (compact version)
    body = _build_decision_body(card)

    # Add action + responsible party
    action = card.action_items[0][:50] if card.action_items else "決策者需確認：是否需要進一步評估"
    responsible = _responsible_party(card.category)
    body.append("")
    body.append(f"建議行動：{action}")
    body.append(f"要問誰：{responsible}")

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

    # --- Per-news slides (banner image top, text below, minimal) ---
    valid_cards = [c for c in cards if c.is_valid_news]
    invalid_cards = [c for c in cards if not c.is_valid_news]

    if valid_cards:
        _slide_section(prs, "新聞深度解析", "News Analysis")
        for i, card in enumerate(valid_cards, 1):
            _slides_news_card(prs, card, i)

    # --- Decision Summary Table (6 columns: #/事件/影響/風險/建議行動/要問誰) ---
    if valid_cards:
        decision_rows = []
        for i, c in enumerate(valid_cards[:8], 1):
            event = c.what_happened[:18] if c.what_happened else "—"
            effect = c.derivable_effects[0][:20] if c.derivable_effects else "待評估"
            risk = c.speculative_effects[0][:20] if c.speculative_effects else "低"
            action = c.action_items[0][:25] if c.action_items else "持續觀察"
            responsible = _responsible_party(c.category)
            decision_rows.append([
                str(i), event, effect, risk, action, responsible,
            ])
        _add_table_slide(
            prs, "決策摘要表  Decision Matrix",
            ["#", "事件", "影響", "風險", "建議行動", "要問誰"],
            decision_rows,
        )

    # --- Invalid items (if any) ---
    for i, card in enumerate(invalid_cards, len(valid_cards) + 1):
        _slides_news_card(prs, card, i)

    # --- System Status (with management interpretation) ---
    # Credibility statement
    if health.success_rate >= 80:
        credibility = f"今日資料可信度良好（完整率 {health.success_rate:.0f}%），決策依據充分"
    elif health.success_rate >= 50:
        credibility = f"今日資料可信度中等（完整率 {health.success_rate:.0f}%），部分結論需保守解讀"
    else:
        credibility = f"今日資料可信度偏低（完整率 {health.success_rate:.0f}%），建議交叉驗證後再做決策"

    metrics_lines = [
        credibility,
        "",
        f"中位數處理延遲：{health.p50_latency:.1f}s｜高延遲指標：{health.p95_latency:.1f}s",
        f"雜訊清除：{health.entity_noise_removed} 筆",
        "",
        f"整體狀態：{health.traffic_light_emoji} {health.traffic_light_label}",
    ]

    # Top 2 fail reasons with decision impact
    if health.fail_reasons:
        metrics_lines.append("")
        metrics_lines.append("需要處理的風險：")
        sorted_reasons = sorted(health.fail_reasons.items(), key=lambda x: -x[1])
        for reason, count in sorted_reasons[:2]:
            translated = translate_fail_reason(reason)
            metrics_lines.append(f"  • {translated}（{count} 次）→ 可能影響資料涵蓋範圍")

    _slide_text(prs, "系統運作概況", metrics_lines)

    # --- Next Steps ---
    next_items = [
        "檢視今日新聞與業務的關聯性，決定是否列入決策議程",
        "針對高風險事件指派追蹤負責人",
        "回顧過去一週趨勢，識別重複模式",
        "確認系統運作狀態，必要時調整來源",
    ]
    _slide_conclusion(prs, next_items)

    prs.save(str(output_path))
    log.info("Executive PPTX generated: %s", output_path)
    return output_path
