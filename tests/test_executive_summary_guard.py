"""Executive Summary guard tests — 5 rules that must always hold.

Guards:
1. Output is 3–5 sentences (split by 。or .)
2. No bullet / list syntax (1. 2. • - First Second Third 首先 其次 再次)
3. No AI filler templates
4. Must contain ≥1 business vocabulary word
5. Zero-event case: non-empty, mentions "沒有需要管理層立即關注", no "無資料" / "N/A"
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.content_strategy import build_executive_summary, SUMMARY_TONE_LIBRARY
from schemas.education_models import EduNewsCard


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_card(idx: int = 1) -> EduNewsCard:
    return EduNewsCard(
        item_id=f"news_{idx:03d}",
        is_valid_news=True,
        title_plain=f"Test News Title {idx}",
        what_happened="Something important happened in the tech world.",
        why_important="This affects the entire industry supply chain.",
        focus_action="Monitor subsequent announcements from major players.",
        metaphor="Like upgrading a factory assembly line.",
        fact_check_confirmed=["Fact A confirmed", "Fact B confirmed"],
        fact_check_unverified=["Claim X needs verification"],
        evidence_lines=["Evidence: original text excerpt here"],
        technical_interpretation="Technical analysis of the event.",
        derivable_effects=["Direct effect on market pricing"],
        speculative_effects=["Possible regulatory response"],
        observation_metrics=["Watch industry adoption rate"],
        action_items=["This week: research latest reports"],
        source_url="https://example.com/news",
        category="tech",
        signal_strength=0.8,
        final_score=8.0,
        source_name="TestSource",
    )


def _make_cards(n: int = 3) -> list[EduNewsCard]:
    return [_make_card(i) for i in range(1, n + 1)]


def _join(lines: list[str]) -> str:
    return "".join(lines)


# ---------------------------------------------------------------------------
# Guard 1: 3–5 sentences
# ---------------------------------------------------------------------------

class TestGuard1SentenceCount:
    """Output must contain 3–5 sentences (split by 。 or .)."""

    def _count_sentences(self, lines: list[str]) -> int:
        text = _join(lines)
        # Split by Chinese or Western period
        parts = re.split(r"[。.]", text)
        # Filter out empty fragments
        return len([p for p in parts if p.strip()])

    def test_with_events(self):
        lines = build_executive_summary(_make_cards(3), tone="neutral")
        count = self._count_sentences(lines)
        assert 3 <= count <= 5, f"Expected 3-5 sentences, got {count}"

    def test_with_one_event(self):
        lines = build_executive_summary(_make_cards(1), tone="neutral")
        count = self._count_sentences(lines)
        assert 3 <= count <= 5, f"Expected 3-5 sentences, got {count}"

    def test_with_zero_events(self):
        lines = build_executive_summary([], tone="neutral")
        count = self._count_sentences(lines)
        assert 3 <= count <= 5, f"Expected 3-5 sentences, got {count}"


# ---------------------------------------------------------------------------
# Guard 2: No bullet / list syntax
# ---------------------------------------------------------------------------

BULLET_PATTERNS = re.compile(
    r"^\s*(\d+[.)]\s)"       # 1. 2) etc.
    r"|^\s*[•\-\*]\s"        # • - * bullets
    r"|^\s*(First|Second|Third|Finally)\b"  # English ordinals
    r"|^\s*(首先|其次|再次|最後)\b",         # Chinese ordinals
    re.MULTILINE,
)


class TestGuard2NoBullets:
    """Output must not contain bullet or list syntax."""

    def test_no_bullets_with_events(self):
        lines = build_executive_summary(_make_cards(3), tone="neutral")
        text = "\n".join(lines)
        match = BULLET_PATTERNS.search(text)
        assert match is None, f"Found bullet/list syntax: {match.group()!r}"

    def test_no_bullets_zero_events(self):
        lines = build_executive_summary([], tone="neutral")
        text = "\n".join(lines)
        match = BULLET_PATTERNS.search(text)
        assert match is None, f"Found bullet/list syntax: {match.group()!r}"


# ---------------------------------------------------------------------------
# Guard 3: No AI filler templates
# ---------------------------------------------------------------------------

AI_FILLER = [
    "在當今快速變化的",
    "隨著科技的發展",
    "總體而言",
    "綜上所述",
    "As part of its mission",
    "This highlights the importance",
    "This reflects the growing trend",
]


class TestGuard3NoAIFiller:
    """Output must not contain generic AI filler phrases."""

    @pytest.mark.parametrize("tone", list(SUMMARY_TONE_LIBRARY.keys()))
    def test_no_filler_all_tones(self, tone):
        lines = build_executive_summary(_make_cards(3), tone=tone)
        text = _join(lines)
        for filler in AI_FILLER:
            assert filler not in text, f"AI filler found: {filler!r}"

    def test_no_filler_zero_events(self):
        lines = build_executive_summary([], tone="neutral")
        text = _join(lines)
        for filler in AI_FILLER:
            assert filler not in text, f"AI filler found: {filler!r}"


# ---------------------------------------------------------------------------
# Guard 4: Must contain ≥1 business vocabulary word
# ---------------------------------------------------------------------------

BUSINESS_WORDS = ["市場", "競爭", "風險", "機會", "策略", "投資", "產品", "客戶", "成本", "成長"]


class TestGuard4BusinessVocab:
    """Output must contain at least one business vocabulary word."""

    def test_has_business_word_with_events(self):
        lines = build_executive_summary(_make_cards(3), tone="neutral")
        text = _join(lines)
        found = [w for w in BUSINESS_WORDS if w in text]
        assert len(found) >= 1, f"No business vocab found in: {text[:200]}"

    @pytest.mark.parametrize("tone", list(SUMMARY_TONE_LIBRARY.keys()))
    def test_has_business_word_all_tones(self, tone):
        lines = build_executive_summary(_make_cards(2), tone=tone)
        text = _join(lines)
        found = [w for w in BUSINESS_WORDS if w in text]
        assert len(found) >= 1, f"No business vocab in tone={tone}: {text[:200]}"


# ---------------------------------------------------------------------------
# Guard 5: Zero-event case
# ---------------------------------------------------------------------------


class TestGuard5ZeroEvents:
    """build_executive_summary([]) must be non-empty, mention no-action-needed,
    and never contain '無資料' or 'N/A'."""

    def test_non_empty(self):
        lines = build_executive_summary([], tone="neutral")
        assert len(lines) > 0, "Zero-event summary is empty"
        assert all(line.strip() for line in lines), "Contains empty line"

    def test_mentions_no_immediate_action(self):
        lines = build_executive_summary([], tone="neutral")
        text = _join(lines)
        assert "沒有需要管理層立即關注" in text, (
            f"Missing '沒有需要管理層立即關注' in: {text[:200]}"
        )

    def test_no_invalid_placeholders(self):
        lines = build_executive_summary([], tone="neutral")
        text = _join(lines)
        assert "無資料" not in text, "Contains '無資料'"
        assert "N/A" not in text, "Contains 'N/A'"

    @pytest.mark.parametrize("tone", list(SUMMARY_TONE_LIBRARY.keys()))
    def test_zero_events_all_tones(self, tone):
        lines = build_executive_summary([], tone=tone)
        assert len(lines) >= 3, f"tone={tone} returned < 3 lines"
        text = _join(lines)
        assert "無資料" not in text
        assert "N/A" not in text
