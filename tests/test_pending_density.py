"""Pending Decisions slide evidence-density tests.

Validates that the Pending Decisions slide in the Executive PPTX meets
minimum evidence requirements without relying on text_chars skip guards.

Requirements (all unconditional):
  - terms  >= PENDING_MIN_TERMS
  - numbers >= PENDING_MIN_NUMBERS
  - sentences >= PENDING_MIN_SENTENCES
  - density_score >= EXEC_DENSITY_THRESHOLDS["pending"]
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import config.settings as settings
from core.ppt_generator import generate_executive_ppt
from schemas.education_models import EduNewsCard, SystemHealthReport
from scripts.diagnostics_pptx import slide_density_audit


def _health() -> SystemHealthReport:
    return SystemHealthReport(success_rate=80.0, p50_latency=1.0, p95_latency=3.0)


def _rich_cards() -> list[EduNewsCard]:
    return [
        EduNewsCard(
            item_id=f"pending-{i:03d}",
            is_valid_news=True,
            title_plain=title,
            what_happened=what,
            why_important=why,
            source_name=src,
            source_url=url,
            final_score=score,
            category="AI",
        )
        for i, (title, what, why, src, url, score) in enumerate([
            (
                "NVIDIA H200 GPU launched at $30k",
                "NVIDIA launched the H200 GPU with 141GB HBM3e memory for $30k per unit.",
                "Impacts AI training costs globally for LLM model development.",
                "TechCrunch",
                "https://techcrunch.com/nvidia-h200",
                9.0,
            ),
            (
                "OpenAI releases GPT-5 with 1M context",
                "OpenAI announced GPT-5 with 1 million token context window for enterprise RAG.",
                "Enables new LLM use cases with large AI model context windows.",
                "TheVerge",
                "https://theverge.com/openai-gpt5",
                8.5,
            ),
            (
                "Anthropic Claude reaches 100M users",
                "Anthropic reported Claude AI model reached 100 million monthly active users.",
                "Claude becomes a major AI competitor challenging ChatGPT and GPT models.",
                "Reuters",
                "https://reuters.com/anthropic-claude-100m",
                8.0,
            ),
        ])
    ]


def _gen_pptx(tmp_path: Path) -> Path:
    pptx_path = tmp_path / "test_pending_density.pptx"
    with patch("core.ppt_generator.get_news_image", return_value=None):
        generate_executive_ppt(
            cards=_rich_cards(),
            health=_health(),
            report_time="2026-01-01 09:00",
            total_items=3,
            output_path=pptx_path,
        )
    return pptx_path


def _get_pending_slide(results: list[dict]) -> dict:
    for r in results:
        if "Pending" in r["title"] or "待決" in r["title"]:
            return r
    raise AssertionError("Pending Decisions slide not found in density audit")


class TestPendingDecisionsDensity:
    """All assertions are unconditional — no text_chars skip permitted."""

    def test_pending_slide_exists(self, tmp_path: Path) -> None:
        pptx = _gen_pptx(tmp_path)
        results = slide_density_audit(pptx)
        slide = _get_pending_slide(results)  # raises if not found
        assert slide is not None

    def test_pending_has_required_terms(self, tmp_path: Path) -> None:
        pptx = _gen_pptx(tmp_path)
        results = slide_density_audit(pptx)
        slide = _get_pending_slide(results)
        assert slide["terms"] >= settings.PENDING_MIN_TERMS, (
            f"Pending terms={slide['terms']} < required={settings.PENDING_MIN_TERMS}. "
            f"all_text={slide['all_text'][:200]!r}"
        )

    def test_pending_has_required_numbers(self, tmp_path: Path) -> None:
        pptx = _gen_pptx(tmp_path)
        results = slide_density_audit(pptx)
        slide = _get_pending_slide(results)
        assert slide["numbers"] >= settings.PENDING_MIN_NUMBERS, (
            f"Pending numbers={slide['numbers']} < required={settings.PENDING_MIN_NUMBERS}. "
            f"all_text={slide['all_text'][:200]!r}"
        )

    def test_pending_has_required_sentences(self, tmp_path: Path) -> None:
        pptx = _gen_pptx(tmp_path)
        results = slide_density_audit(pptx)
        slide = _get_pending_slide(results)
        assert slide["sentences"] >= settings.PENDING_MIN_SENTENCES, (
            f"Pending sentences={slide['sentences']} < required={settings.PENDING_MIN_SENTENCES}. "
            f"all_text={slide['all_text'][:200]!r}"
        )

    def test_pending_density_score_meets_threshold(self, tmp_path: Path) -> None:
        threshold = settings.EXEC_DENSITY_THRESHOLDS["pending"]
        pptx = _gen_pptx(tmp_path)
        results = slide_density_audit(pptx)
        slide = _get_pending_slide(results)
        assert slide["density_score"] >= threshold, (
            f"Pending density={slide['density_score']} < required={threshold}. "
            f"terms={slide['terms']}, numbers={slide['numbers']}, "
            f"sentences={slide['sentences']}, text_chars={slide['text_chars']}"
        )
