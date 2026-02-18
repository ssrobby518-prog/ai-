"""Per-slide density threshold tests for Executive PPTX output.

Validates:
  1) slide_density_audit() returns complete per-slide metrics
  2) Overview / Event Ranking / Pending Decisions meet EXEC_REQUIRED_SLIDE_DENSITY
  3) No forbidden fragments appear in any slide
  4) Fragment-detection unit tests (bad inputs don't survive pipeline)
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

import config.settings as settings
from core.ppt_generator import generate_executive_ppt
from schemas.education_models import EduNewsCard, SystemHealthReport
from scripts.diagnostics_pptx import slide_density_audit
from utils.text_quality import is_fragment, trim_trailing_fragment


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _health() -> SystemHealthReport:
    return SystemHealthReport(success_rate=80.0, p50_latency=1.0, p95_latency=3.0)


def _rich_cards() -> list[EduNewsCard]:
    """Cards with strong AI-domain content — density gate must pass."""
    return [
        EduNewsCard(
            item_id=f"density-{i:03d}",
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


def _gen_pptx(tmp_path: Path, cards: list[EduNewsCard]) -> Path:
    pptx_path = tmp_path / "test_density.pptx"
    with patch("core.ppt_generator.get_news_image", return_value=None):
        generate_executive_ppt(
            cards=cards,
            health=_health(),
            report_time="2026-01-01 09:00",
            total_items=len(cards),
            output_path=pptx_path,
        )
    return pptx_path


# ---------------------------------------------------------------------------
# Test: slide_density_audit() API contract
# ---------------------------------------------------------------------------

class TestSlideDensityAuditContract:

    def test_returns_list_of_dicts(self, tmp_path: Path) -> None:
        pptx = _gen_pptx(tmp_path, _rich_cards())
        results = slide_density_audit(pptx)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_required_keys_present(self, tmp_path: Path) -> None:
        pptx = _gen_pptx(tmp_path, _rich_cards())
        required = {
            "slide_index", "title", "text_chars",
            "table_cells_total", "table_cells_nonempty",
            "terms", "numbers", "sentences", "density_score", "all_text",
        }
        for r in slide_density_audit(pptx):
            assert required.issubset(r.keys()), f"Missing keys in slide {r.get('slide_index')}"

    def test_slide_index_sequential(self, tmp_path: Path) -> None:
        pptx = _gen_pptx(tmp_path, _rich_cards())
        results = slide_density_audit(pptx)
        for i, r in enumerate(results, 1):
            assert r["slide_index"] == i

    def test_density_score_in_range(self, tmp_path: Path) -> None:
        pptx = _gen_pptx(tmp_path, _rich_cards())
        for r in slide_density_audit(pptx):
            assert 0 <= r["density_score"] <= 100, (
                f"slide {r['slide_index']}: score {r['density_score']} out of [0,100]"
            )

    def test_text_chars_nonnegative(self, tmp_path: Path) -> None:
        pptx = _gen_pptx(tmp_path, _rich_cards())
        for r in slide_density_audit(pptx):
            assert r["text_chars"] >= 0


# ---------------------------------------------------------------------------
# Test: 3 key slides meet density threshold
# ---------------------------------------------------------------------------

class TestKeySlidesDensity:
    """Overview / Event Ranking / Pending Decisions must meet per-type density thresholds.

    No text_chars skip is allowed — all three slides must pass unconditionally.
    """

    def _find(self, results: list[dict], *patterns: str) -> dict | None:
        for r in results:
            for pat in patterns:
                if pat.lower() in r["title"].lower():
                    return r
        return None

    def test_overview_density(self, tmp_path: Path) -> None:
        threshold = settings.EXEC_DENSITY_THRESHOLDS["overview"]
        pptx = _gen_pptx(tmp_path, _rich_cards())
        results = slide_density_audit(pptx)
        slide = self._find(results, "Overview", "總覽")
        assert slide is not None, "Overview slide not found in density audit"
        assert slide["density_score"] >= threshold, (
            f"Overview density={slide['density_score']} < required={threshold}"
        )

    def test_event_ranking_density(self, tmp_path: Path) -> None:
        threshold = settings.EXEC_DENSITY_THRESHOLDS["ranking"]
        pptx = _gen_pptx(tmp_path, _rich_cards())
        results = slide_density_audit(pptx)
        slide = self._find(results, "Event Ranking", "排行")
        assert slide is not None, "Event Ranking slide not found in density audit"
        assert slide["density_score"] >= threshold, (
            f"Event Ranking density={slide['density_score']} < required={threshold}"
        )

    def test_pending_decisions_density(self, tmp_path: Path) -> None:
        threshold = settings.EXEC_DENSITY_THRESHOLDS["pending"]
        pptx = _gen_pptx(tmp_path, _rich_cards())
        results = slide_density_audit(pptx)
        slide = self._find(results, "Pending", "待決")
        assert slide is not None, "Pending Decisions slide not found"
        assert slide["density_score"] >= threshold, (
            f"Pending Decisions density={slide['density_score']} < required={threshold}"
        )

    def test_no_text_chars_skip_in_key_slide_density_tests(self) -> None:
        """Confirm no text_chars skip guards remain in this test file."""
        source = Path(__file__).read_text(encoding="utf-8")
        # Build pattern at runtime so this assertion doesn't self-match
        guard = "if slide[" + '"text_chars"' + "]"
        assert guard not in source, (
            "text_chars skip guard detected — unconditional density assertions are required"
        )

    def test_overview_table_nonempty_ratio(self, tmp_path: Path) -> None:
        """Overview table must have ≥ EXEC_TABLE_MIN_NONEMPTY_RATIO non-empty cells."""
        pptx = _gen_pptx(tmp_path, _rich_cards())
        results = slide_density_audit(pptx)
        slide = self._find(results, "Overview", "總覽")
        assert slide is not None
        total = slide["table_cells_total"]
        if total > 0:
            ratio = slide["table_cells_nonempty"] / total
            assert ratio >= settings.EXEC_TABLE_MIN_NONEMPTY_RATIO, (
                f"Overview table nonempty ratio {ratio:.2%} < {settings.EXEC_TABLE_MIN_NONEMPTY_RATIO:.2%}"
            )

    def test_event_ranking_table_nonempty_ratio(self, tmp_path: Path) -> None:
        pptx = _gen_pptx(tmp_path, _rich_cards())
        results = slide_density_audit(pptx)
        slide = self._find(results, "Event Ranking", "排行")
        assert slide is not None
        total = slide["table_cells_total"]
        if total > 0:
            ratio = slide["table_cells_nonempty"] / total
            assert ratio >= settings.EXEC_TABLE_MIN_NONEMPTY_RATIO, (
                f"Event Ranking table ratio {ratio:.2%} < {settings.EXEC_TABLE_MIN_NONEMPTY_RATIO:.2%}"
            )


# ---------------------------------------------------------------------------
# Test: Forbidden fragments absent from all slides
# ---------------------------------------------------------------------------

class TestForbiddenFragmentsAbsent:

    def test_no_forbidden_fragments_in_rich_deck(self, tmp_path: Path) -> None:
        pptx = _gen_pptx(tmp_path, _rich_cards())
        results = slide_density_audit(pptx)
        for slide_data in results:
            txt = slide_data.get("all_text", "")
            for frag in settings.EXEC_FORBIDDEN_FRAGMENTS:
                assert frag not in txt, (
                    f"Forbidden fragment '{frag}' found in slide "
                    f"{slide_data['slide_index']}: {slide_data['title']!r}"
                )

    def test_pending_no_forbidden_fragments(self, tmp_path: Path) -> None:
        pptx = _gen_pptx(tmp_path, _rich_cards())
        results = slide_density_audit(pptx)
        pending = next(
            (r for r in results if "Pending" in r["title"] or "待決" in r["title"]),
            None,
        )
        assert pending is not None, "Pending Decisions slide not found"
        for frag in settings.EXEC_FORBIDDEN_FRAGMENTS:
            assert frag not in pending.get("all_text", ""), (
                f"Forbidden fragment '{frag}' found in Pending Decisions"
            )


# ---------------------------------------------------------------------------
# Test: Fragment / trailing-token unit tests (bad inputs → caught)
# ---------------------------------------------------------------------------

class TestFragmentDetectionUnit:

    def test_last_july_was_is_in_forbidden_list(self) -> None:
        assert "Last July was" in settings.EXEC_FORBIDDEN_FRAGMENTS

    def test_trailing_tokens_re_catches_connectors(self) -> None:
        pattern = settings.EXEC_FRAGMENT_TRAILING_TOKENS_RE
        for text in ["something but", "analysis of,", "results and", "data from"]:
            assert re.search(pattern, text, re.IGNORECASE), (
                f"EXEC_FRAGMENT_TRAILING_TOKENS_RE missed: {text!r}"
            )

    def test_is_fragment_short_no_entity(self) -> None:
        assert is_fragment("的趨勢") is True
        assert is_fragment("ok") is True

    def test_is_fragment_with_number(self) -> None:
        assert is_fragment("v3.5") is False

    def test_trim_trailing_zh_particle_residue(self) -> None:
        bad = "第一句話。解決方案來適應變化記"  # ends with Chinese trailing particle
        result = trim_trailing_fragment(bad)
        assert not result.endswith("記"), f"Still trailing particle: {result!r}"

    def test_trim_trailing_comma(self) -> None:
        result = trim_trailing_fragment("First sentence. Second part,")
        assert result == "First sentence."

    def test_settings_exec_thresholds_have_expected_defaults(self) -> None:
        assert settings.EXEC_SLIDE_MIN_TEXT_CHARS == 160
        assert settings.EXEC_TABLE_MIN_NONEMPTY_RATIO == 0.60
        assert settings.EXEC_BLOCK_MIN_SENTENCES == 2
        assert settings.EXEC_BLOCK_MIN_EVIDENCE_TERMS == 2
        assert settings.EXEC_BLOCK_MIN_EVIDENCE_NUMBERS == 1
        assert settings.EXEC_REQUIRED_SLIDE_DENSITY == 80
        # Per-type thresholds
        assert settings.EXEC_DENSITY_THRESHOLDS["overview"] == 80
        assert settings.EXEC_DENSITY_THRESHOLDS["ranking"] == 80
        assert settings.EXEC_DENSITY_THRESHOLDS["pending"] == 80
        # Pending evidence minimums
        assert settings.PENDING_MIN_TERMS == 2
        assert settings.PENDING_MIN_NUMBERS == 1
        assert settings.PENDING_MIN_SENTENCES == 1
