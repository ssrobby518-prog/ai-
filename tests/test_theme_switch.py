from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pptx import Presentation

from core.ppt_generator import DARK_BG, LIGHT_BG, generate_executive_ppt
from schemas.education_models import EduNewsCard, SystemHealthReport


def _cards() -> list[EduNewsCard]:
    return [
        EduNewsCard(
            item_id="event-001",
            is_valid_news=True,
            title_plain="NVIDIA launches new GPU architecture",
            what_happened="Launch event with architecture updates.",
            why_important="Impacts AI infrastructure pricing and capacity.",
            source_name="TechCrunch",
            source_url="https://example.com/news",
            final_score=8.0,
        )
    ]


def test_default_theme_is_light(tmp_path: Path) -> None:
    out = tmp_path / "light_default.pptx"
    health = SystemHealthReport(success_rate=80.0, p50_latency=2.0, p95_latency=5.0)

    with patch("core.ppt_generator.get_news_image", return_value=None):
        generate_executive_ppt(
            cards=_cards(),
            health=health,
            report_time="2026-02-15 09:00",
            total_items=1,
            output_path=out,
        )

    prs = Presentation(str(out))
    for slide in prs.slides:
        assert slide.background.fill.fore_color.rgb == LIGHT_BG


def test_dark_theme_can_generate(tmp_path: Path) -> None:
    out = tmp_path / "dark_theme.pptx"
    health = SystemHealthReport(success_rate=80.0, p50_latency=2.0, p95_latency=5.0)

    with patch("core.ppt_generator.get_news_image", return_value=None):
        generate_executive_ppt(
            cards=_cards(),
            health=health,
            report_time="2026-02-15 09:00",
            total_items=1,
            output_path=out,
            theme="dark",
        )

    prs = Presentation(str(out))
    for slide in prs.slides:
        assert slide.background.fill.fore_color.rgb == DARK_BG
