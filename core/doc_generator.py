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
    build_ceo_actions,
    build_ceo_article_blocks,
    build_ceo_brief_blocks,
    build_corp_watch_summary,
    build_decision_card,
    build_executive_summary,
    build_signal_summary,
    build_structured_executive_summary,
    build_term_explainer,
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
    # Cover banner image — ensures DOCX always has at least 1 image
    try:
        img_path = get_news_image("Daily Tech Intelligence Briefing", "科技")
        if img_path.exists():
            doc.add_picture(str(img_path), width=Cm(16))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    except Exception:
        pass
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


def _build_executive_summary(doc: Document, cards: list[EduNewsCard]) -> None:
    """Executive Summary — narrative paragraph, not bullets."""
    _add_heading(doc, "今日重點總覽", level=1)
    subtitle = doc.add_paragraph("Executive Summary")
    subtitle.runs[0].font.size = Pt(12)
    subtitle.runs[0].font.color.rgb = GRAY_COLOR
    _add_divider(doc)

    summary_lines = build_executive_summary(cards, tone="neutral")
    for line in summary_lines:
        p = doc.add_paragraph(sanitize(line))
        p.paragraph_format.space_after = Pt(8)
        for run in p.runs:
            run.font.size = Pt(11)
            run.font.color.rgb = DARK_TEXT
    _add_divider(doc)


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

    # 5. Risks
    risks = article.get("risks", [])
    if risks:
        _add_callout(doc, "主要風險", [f"• {sanitize(r)}" for r in risks[:2]])

    # 6. What to do
    actions = article.get("what_to_do", [])
    if actions:
        _add_callout(doc, "建議下一步", [f"• {sanitize(a)}" for a in actions[:2]])

    # 7. Quote
    if article.get("quote"):
        _add_callout(doc, "關鍵引述", [article["quote"]])

    # 8. Key terms — Notion-style: term + what + CEO concern
    term_items = build_term_explainer(card)
    if term_items:
        term_lines = []
        for it in term_items:
            term_lines.append(f"{it['term']}：{sanitize(it['explain'])}")
            if it.get("biz"):
                term_lines.append(f"  {sanitize(it['biz'])}")
        _add_callout(doc, "重要名詞白話解釋", term_lines)

    # 9. Source
    if card.source_url and card.source_url.startswith("http"):
        p_src = doc.add_paragraph()
        run_src = p_src.add_run(f"原始來源：{card.source_url}")
        run_src.font.size = Pt(9)
        run_src.font.color.rgb = GRAY_COLOR


def _build_structured_summary(doc: Document, cards: list[EduNewsCard],
                              tone: str = "neutral") -> None:
    """Structured Executive Summary — 5 sections matching PPT."""
    _add_heading(doc, "Structured Summary", level=1)
    _add_divider(doc)

    summary = build_structured_executive_summary(cards, tone)
    section_map = [
        ("AI Trends", summary.get("ai_trends", [])),
        ("Tech Landing", summary.get("tech_landing", [])),
        ("Market Competition", summary.get("market_competition", [])),
        ("Opportunities & Risks", summary.get("opportunities_risks", [])),
        ("Recommended Actions", summary.get("recommended_actions", [])),
    ]
    for sec_title, items in section_map:
        _add_callout(doc, sec_title, [sanitize(it) for it in items[:3]])
    _add_divider(doc)


def _build_brief_card_section(doc: Document, card: EduNewsCard, idx: int) -> None:
    """CEO Brief card — WHAT HAPPENED + WHY IT MATTERS (Q&A), matching PPT."""
    brief = build_ceo_brief_blocks(card)
    _add_divider(doc)

    # ── WHAT HAPPENED ──
    _add_heading(doc, f"#{idx}  {brief['title']}", level=2)

    # AI trend liner
    p_trend = doc.add_paragraph()
    run_trend = p_trend.add_run(sanitize(brief["ai_trend_liner"]))
    run_trend.font.size = Pt(11)
    run_trend.font.color.rgb = ACCENT_COLOR
    run_trend.bold = True

    # Image query (text reference)
    p_img = doc.add_paragraph()
    run_img = p_img.add_run(f"Image: {sanitize(brief['image_query'])}")
    run_img.font.size = Pt(9)
    run_img.font.color.rgb = GRAY_COLOR

    # Embedded image
    try:
        img_path = get_news_image(card.title_plain, card.category)
        if img_path.exists():
            doc.add_picture(str(img_path), width=Cm(14))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    except Exception:
        pass

    # Event one-liner
    _add_bold_label(doc, "事件", brief["event_liner"])

    # Data card (1-3 metrics)
    data_items = brief.get("data_card", [])
    if data_items:
        dc_lines = [f"{it['value']}  {it['label']}" for it in data_items[:3]]
        _add_callout(doc, "Data Card", dc_lines)

    # Chart spec (text block)
    chart = brief.get("chart_spec", {})
    if chart:
        chart_lines = [
            f"Chart Type: {chart.get('type', 'bar')}",
            f"Labels: {', '.join(chart.get('labels', []))}",
            f"Values: {', '.join(str(v) for v in chart.get('values', []))}",
        ]
        _add_callout(doc, "Chart Spec", chart_lines)

    # CEO metaphor (italic)
    metaphor = brief.get("ceo_metaphor", "")
    if metaphor:
        p_meta = doc.add_paragraph()
        run_meta = p_meta.add_run(sanitize(metaphor))
        run_meta.font.size = Pt(11)
        run_meta.font.color.rgb = GRAY_COLOR
        run_meta.italic = True

    # ── WHY IT MATTERS (Q&A) ──
    _add_heading(doc, f"#{idx}  WHY IT MATTERS", level=3)

    # Q1
    _add_bold_label(doc, "Q1：這件事的商業意義", brief["q1_meaning"])

    # Q2
    _add_bold_label(doc, "Q2：對公司的影響", brief["q2_impact"])

    # Q3 (numbered actions ≤3)
    actions = brief.get("q3_actions", [])
    q3_lines = [f"{i}. {sanitize(a)}" for i, a in enumerate(actions[:3], 1)]
    _add_callout(doc, "Q3：現在要做什麼", q3_lines)

    # Video source
    videos = brief.get("video_source", [])
    if videos:
        vid = videos[0]
        p_vid = doc.add_paragraph()
        run_vid = p_vid.add_run(
            f"Video: {sanitize(vid.get('title', ''))} — {vid.get('url', '')}")
        run_vid.font.size = Pt(9)
        run_vid.font.color.rgb = GRAY_COLOR

    # Sources
    sources = brief.get("sources", [])
    if sources:
        p_src = doc.add_paragraph()
        run_src = p_src.add_run(f"Source: {sources[0]}")
        run_src.font.size = Pt(9)
        run_src.font.color.rgb = GRAY_COLOR


def _build_signal_thermometer(doc: Document, cards: list[EduNewsCard]) -> None:
    """Signal Thermometer — market heat + signal breakdown, matching PPT."""
    _add_heading(doc, "Signal Thermometer", level=1)
    _add_divider(doc)

    heat = compute_market_heat(cards)
    _add_bold_label(doc, "Market Heat Index", f"{heat['score']} / 100")
    _add_bold_label(doc, "Level", heat["level"])
    _add_bold_label(doc, "趨勢", heat["trend_word"])

    signals = build_signal_summary(cards)
    sig_lines = []
    for sig in signals[:3]:
        source_name = str(sig.get("source_name", "unknown"))
        sig_lines.append(
            f"[{sig['heat'].upper()}] {sig['label']}：{sig['title']} "
            f"({sig['source_count']} sources, source={source_name})"
        )
    _add_callout(doc, "Top Signals", sig_lines if sig_lines else ["今日無明顯訊號"])
    _add_divider(doc)


def _build_corp_watch(
    doc: Document,
    cards: list[EduNewsCard],
    metrics: dict | None = None,
) -> None:
    """Corp Watch — Tier A + Tier B company monitoring, matching PPT."""
    _add_heading(doc, "Corp Watch", level=1)
    _add_divider(doc)

    corp = build_corp_watch_summary(cards, metrics=metrics)
    _add_bold_label(doc, "Total Mentions", str(corp["total_mentions"]))

    if int(corp.get("updates", corp.get("total_mentions", 0))) == 0:
        fail_bits = []
        for item in corp.get("top_fail_reasons", []):
            fail_bits.append(f"{item.get('reason', 'none')} ({item.get('count', 0)})")
        source_bits = []
        for src in corp.get("top_sources", [])[:3]:
            source_bits.append(
                f"{src.get('source_name', 'none')}: items_seen={src.get('items_seen', 0)}, "
                f"gate_pass={src.get('gate_pass', 0)}, gate_soft_pass={src.get('gate_soft_pass', 0)}"
            )
        _add_callout(
            doc,
            "Source Scan Stats",
            [
                f"status: {corp.get('status_message', 'none')}",
                f"sources_total: {corp.get('sources_total', 0)}",
                f"success_count: {corp.get('success_count', 0)}",
                f"fail_count: {corp.get('fail_count', 0)}",
                f"top_fail_reasons: {', '.join(fail_bits) if fail_bits else 'none'}",
                f"top_sources: {' | '.join(source_bits) if source_bits else 'none'}",
            ],
        )
        _add_divider(doc)
        return

    # Tier A
    tier_a_lines = []
    for item in corp["tier_a"][:5]:
        tier_a_lines.append(
            f"{item['name']} — [{item['impact_label']}] "
            f"{sanitize(item['event_title'])}"
        )
    if not tier_a_lines:
        tier_a_lines = ["今日無 Tier A 公司相關事件"]
    _add_callout(doc, "Tier A — Global Leaders", tier_a_lines)

    # Tier B
    tier_b_lines = []
    for item in corp["tier_b"][:4]:
        tier_b_lines.append(
            f"{item['name']} — [{item['impact_label']}] "
            f"{sanitize(item['event_title'])}"
        )
    if not tier_b_lines:
        tier_b_lines = ["今日無 Tier B 公司相關事件"]
    _add_callout(doc, "Tier B — Asia Leaders", tier_b_lines)
    _add_divider(doc)


def _build_event_ranking(doc: Document, cards: list[EduNewsCard]) -> None:
    """Event Ranking — impact-scored table, matching PPT."""
    event_cards = [c for c in cards if c.is_valid_news and not is_non_event_or_index(c)]
    if not event_cards:
        return

    _add_heading(doc, "Event Ranking  事件影響力排行", level=1)

    scored = []
    for c in event_cards[:8]:
        impact = score_event_impact(c)
        scored.append((c, impact))
    scored.sort(key=lambda x: x[1]["impact"], reverse=True)

    rows = []
    for rank, (c, imp) in enumerate(scored, 1):
        dc = build_decision_card(c)
        action = dc["actions"][0] if dc["actions"] else "待確認"
        rows.append([
            str(rank),
            f"{imp['impact']}/5 {imp['label']}",
            sanitize(c.title_plain or "")[:25],
            c.category or "綜合",
            sanitize(action)[:25],
        ])
    _make_simple_table(
        doc, ["Rank", "Impact", "標題", "類別", "Action"], rows
    )
    doc.add_paragraph("")


def _build_recommended_moves(doc: Document, cards: list[EduNewsCard]) -> None:
    """Recommended Moves — MOVE/TEST/WATCH, matching PPT."""
    _add_heading(doc, "Recommended Moves", level=1)
    _add_divider(doc)

    actions = build_ceo_actions(cards)
    if not actions:
        p = doc.add_paragraph("本日無需要立即行動的事項")
        p.runs[0].font.size = Pt(11)
        return

    for act in actions[:6]:
        lines = [
            f"{sanitize(act['detail'])}",
            f"Owner: {act['owner']}",
        ]
        _add_callout(doc, f"[{act['action_type']}] {act['title']}", lines)
    _add_divider(doc)


def _build_decision_matrix(doc: Document, cards: list[EduNewsCard]) -> None:
    """Decision Matrix table — 6 columns, same as PPT."""
    event_cards = [c for c in cards if c.is_valid_news and not is_non_event_or_index(c)]
    if not event_cards:
        return
    _add_heading(doc, "決策摘要表  Decision Matrix", level=1)
    rows = []
    for i, c in enumerate(event_cards[:8], 1):
        dc = build_decision_card(c)
        rows.append([
            str(i),
            sanitize(dc["event"][:18]),
            sanitize(dc["effects"][0][:25]) if dc["effects"] else "缺口",
            sanitize(dc["risks"][0][:25]) if dc["risks"] else "缺口",
            sanitize(dc["actions"][0][:30]) if dc["actions"] else "待確認",
            dc["owner"],
        ])
    _make_simple_table(doc, ["#", "事件", "影響", "風險", "建議行動", "要問誰"], rows)
    doc.add_paragraph("")


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
    metrics: dict | None = None,
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

    # 1. Cover
    _build_cover_section(doc, report_time, total_items, health)

    # 2. Structured Summary (5 sections — new CEO Brief format)
    _build_structured_summary(doc, cards, metrics=metrics)

    # 3. Signal Thermometer (v5)
    _build_signal_thermometer(doc, cards)

    # 4. Corp Watch (v5)
    _build_corp_watch(doc, cards, metrics=metrics)

    # 5. Key Takeaways
    _build_key_takeaways(doc, cards, total_items, metrics=metrics)

    # 6. Overview Table
    _build_overview_table(doc, cards)

    # 7. Event Ranking (v5)
    _build_event_ranking(doc, cards)

    # 8. Per-event: CEO Brief card (WHAT HAPPENED + WHY IT MATTERS)
    event_cards = [c for c in cards if c.is_valid_news and not is_non_event_or_index(c)]
    for i, card in enumerate(event_cards, 1):
        _build_brief_card_section(doc, card, i)

    # 9. Recommended Moves (v5)
    _build_recommended_moves(doc, cards)

    # 10. Decision Matrix
    _build_decision_matrix(doc, cards)

    # 11. Pending Decisions
    _build_conclusion_section(doc, cards)

    doc.save(str(output_path))
    log.info("Executive DOCX generated: %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# v5.2.2 overrides (append-only quality hotfix layer)
# ---------------------------------------------------------------------------

_v521_build_structured_summary = _build_structured_summary
_v521_build_key_takeaways = _build_key_takeaways


def _build_structured_summary(
    doc: Document,
    cards: list[EduNewsCard],
    tone: str = "neutral",
    metrics: dict | None = None,
) -> None:
    """Structured summary with metric-backed no-event fallback."""
    _add_heading(doc, "Structured Summary", level=1)
    _add_divider(doc)

    summary = build_structured_executive_summary(cards, tone, metrics=metrics or {})
    section_map = [
        ("AI Trends", summary.get("ai_trends", [])),
        ("Tech Landing", summary.get("tech_landing", [])),
        ("Market Competition", summary.get("market_competition", [])),
        ("Opportunities & Risks", summary.get("opportunities_risks", [])),
        ("Recommended Actions", summary.get("recommended_actions", [])),
    ]
    for sec_title, items in section_map:
        _add_callout(doc, sec_title, [sanitize(it) for it in items[:3]])
    _add_divider(doc)


def _build_key_takeaways(
    doc: Document,
    cards: list[EduNewsCard],
    total_items: int,
    metrics: dict | None = None,
) -> None:
    """Key takeaways with stats-backed no-event fallback."""
    event_cards = [c for c in cards if c.is_valid_news and not is_non_event_or_index(c)]
    lines = [f"Total scanned: {total_items} | Event candidates: {len(event_cards)}"]
    for c in event_cards[:3]:
        dc = build_decision_card(c)
        lines.append(f"- {sanitize(c.title_plain[:40])} - {dc['event']}")
    if not event_cards:
        fetched_total = int((metrics or {}).get("fetched_total", total_items))
        gate_pass_total = int((metrics or {}).get("gate_pass_total", sum(1 for c in cards if c.is_valid_news)))
        sources_total = int((metrics or {}).get("sources_total", 0))
        after_filter_total = int((metrics or {}).get("after_filter_total", gate_pass_total))
        lines.extend(
            [
                f"Scan overview: fetched_total={fetched_total}, gate_pass_total={gate_pass_total}.",
                f"Coverage: sources_total={sources_total}, after_filter_total={after_filter_total}.",
                "No event candidate crossed action threshold; continue monitoring with source-level diagnostics.",
            ]
        )
    _add_callout(doc, "Key Takeaways", lines)
    _add_divider(doc)
