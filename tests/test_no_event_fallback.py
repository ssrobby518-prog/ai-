from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pptx import Presentation

from core.content_strategy import build_corp_watch_summary, build_signal_summary
from core.ppt_generator import generate_executive_ppt
from schemas.education_models import EduNewsCard, SystemHealthReport


def _no_event_cards() -> list[EduNewsCard]:
    return [
        EduNewsCard(
            item_id="index-001",
            is_valid_news=True,
            title_plain="As part of its mission to preserve the web",
            what_happened="Curated links and archive index",
            why_important="Reference page, not an event",
            source_name="TechCrunch",
            source_url="https://example.com/index",
            final_score=2.0,
        ),
        EduNewsCard(
            item_id="invalid-001",
            is_valid_news=False,
            title_plain="Login page captured",
            what_happened="Please sign in to continue",
            invalid_reason="system banner",
            invalid_cause="blocked",
            source_name="HackerNews",
            source_url="https://example.com/login",
            final_score=0.0,
        ),
    ]


def _all_text(prs: Presentation) -> str:
    buf: list[str] = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for p in shape.text_frame.paragraphs:
                    if p.text.strip():
                        buf.append(p.text.strip())
    return "\n".join(buf)


def test_no_event_signal_summary_top3() -> None:
    signals = build_signal_summary(_no_event_cards())
    assert len(signals) >= 3
    for sig in signals[:3]:
        assert "signal_text" in sig
        assert "platform_count" in sig
        assert "heat_score" in sig


def test_no_event_corp_watch_includes_scan_stats() -> None:
    corp = build_corp_watch_summary(_no_event_cards())
    assert corp.get("updates", -1) == 0
    assert "sources_total" in corp
    assert "success_count" in corp
    assert "fail_count" in corp
    assert "top_fail_reasons" in corp
    assert corp["sources_total"] >= 1


def test_no_event_still_generates_complete_deck(tmp_path: Path) -> None:
    out = tmp_path / "no_event.pptx"
    health = SystemHealthReport(success_rate=0.0, p50_latency=0.0, p95_latency=0.0)
    cards = _no_event_cards()

    with patch("core.ppt_generator.get_news_image", return_value=None):
        generate_executive_ppt(
            cards=cards,
            health=health,
            report_time="2026-02-15 09:00",
            total_items=len(cards),
            output_path=out,
        )

    prs = Presentation(str(out))
    assert len(prs.slides) >= 7

    text = _all_text(prs)
    assert "Signal Thermometer" in text
    assert "Corp Watch" in text
    assert "sources_total" in text
