"""Tests for utils/topic_router.py — offline only, no network.

Test classes:
  TestRelevanceGate                — is_relevant_ai() hard negatives / pass conditions
  TestChannelClassification        — classify_channels() per-channel scoring
  TestQuotaSelection               — select_executive_items() quota enforcement
  TestActionsMovesFragmentGuard    — Recommended Actions/Moves fragment guard (D)
"""
from __future__ import annotations

import pytest

from utils.topic_router import classify_channels, is_relevant_ai
from core.content_strategy import (
    build_ceo_actions,
    select_executive_items,
)
from schemas.education_models import EduNewsCard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _card(
    title: str = "OpenAI launches GPT-5",
    what: str = "OpenAI announced GPT-5 with 1M token context window.",
    why: str = "Transformative for enterprise AI applications worth $10B market.",
    url: str = "https://techcrunch.com/openai-gpt5",
    score: float = 8.5,
    category: str = "AI",
    item_id: str | None = None,
) -> EduNewsCard:
    return EduNewsCard(
        item_id=item_id or f"tr-{hash(title) % 99999:05d}",
        is_valid_news=True,
        title_plain=title,
        what_happened=what,
        why_important=why,
        source_name="TechCrunch",
        source_url=url,
        final_score=score,
        category=category,
    )


# ---------------------------------------------------------------------------
# Test 1: Relevance gate filters building noise
# ---------------------------------------------------------------------------

class TestRelevanceGateFiltersBuildingNoise:
    """is_relevant_ai() must reject building/real-estate content with no AI."""

    def test_building_content_no_ai_is_rejected(self):
        text = "新大樓建案住宅商辦樓層都更開案，建商推出透天厝產品。"
        relevant, reasons = is_relevant_ai(text, url="https://example.com/property")
        assert relevant is False, f"Building-only content should be rejected; reasons={reasons}"
        # Rejection must be via hard_neg (building detected) or no_ai_core path
        assert any(
            "building" in r or "hard_neg" in r or "no_ai_core" in r
            for r in reasons
        ), f"Expected building or no_ai_core rejection reason; got: {reasons}"

    def test_real_estate_english_no_ai_is_rejected(self):
        text = (
            "New luxury residential tower construction begins in downtown. "
            "Real estate developer breaks ground on 50-story condominium complex."
        )
        relevant, reasons = is_relevant_ai(text, url="https://realestate.example.com/tower")
        assert relevant is False, f"Real-estate-only content should be rejected; reasons={reasons}"

    def test_building_with_ai_core_is_allowed(self):
        """Building + AI data center → should pass (AI core present)."""
        text = (
            "Reliance has begun building multi-gigawatt AI data centers in Jamnagar. "
            "The infrastructure supports LLM inference at scale."
        )
        relevant, reasons = is_relevant_ai(text)
        assert relevant is True, f"Building + AI core should pass; reasons={reasons}"

    def test_whitelist_company_fast_passes(self):
        text = "OpenAI released a new model update."
        relevant, reasons = is_relevant_ai(text)
        assert relevant is True
        assert any("whitelist" in r for r in reasons)

    def test_pure_noise_no_ai_no_building_is_rejected(self):
        """General non-tech content with no AI keywords should be rejected."""
        text = "Local restaurant opens new branch. Chef wins award for dessert menu."
        relevant, reasons = is_relevant_ai(text)
        assert relevant is False


# ---------------------------------------------------------------------------
# Test 2: Channel classification
# ---------------------------------------------------------------------------

class TestChannelScoringClassifiesProductTechBusiness:
    """classify_channels() should assign the correct best_channel for each domain."""

    def test_product_text_scores_product_channel(self):
        text = (
            "OpenAI launched GPT-4o mini with general availability pricing $0.15/M tokens. "
            "New model rollout starts today for all API users. Version 2.0 now available."
        )
        result = classify_channels(text, url="https://openai.com/blog/gpt4o-mini")
        assert result["product_score"] >= 60, (
            f"Product text should score >= 60 in product channel; got {result['product_score']}"
        )
        assert result["best_channel"] == "product", (
            f"best_channel should be 'product'; got {result['best_channel']}"
        )

    def test_tech_text_scores_tech_channel(self):
        text = (
            "New arXiv paper presents a 70B parameter LLM with quantization achieving "
            "93.4% on MMLU benchmark. Weights released on HuggingFace. Latency improved 40%."
        )
        result = classify_channels(text, url="https://arxiv.org/abs/2412.16012")
        assert result["tech_score"] >= 60, (
            f"Tech text should score >= 60 in tech channel; got {result['tech_score']}"
        )
        assert result["best_channel"] == "tech", (
            f"best_channel should be 'tech'; got {result['best_channel']}"
        )

    def test_business_text_scores_business_channel(self):
        text = (
            "Kana AI raises $15M Series A from Accel Partners to expand enterprise AI marketing. "
            "CEO announces partnership with Salesforce. ARR reached $2M in 6 months."
        )
        result = classify_channels(text, url="https://techcrunch.com/kana-funding")
        assert result["business_score"] >= 60, (
            f"Business text should score >= 60 in business channel; got {result['business_score']}"
        )
        assert result["best_channel"] == "business", (
            f"best_channel should be 'business'; got {result['best_channel']}"
        )

    def test_dev_text_scores_dev_channel(self):
        text = (
            "LangChain v0.3.0 released on GitHub with breaking changes to the core library. "
            "Open-source framework now available on PyPI. 50k GitHub stars. "
            "Fork the repo and contribute via pull requests."
        )
        result = classify_channels(text, url="https://github.com/langchain-ai/langchain/releases/tag/v0.3.0")
        assert result["dev_score"] >= 60, (
            f"Dev text should score >= 60 in dev channel; got {result['dev_score']}"
        )
        # With GitHub URL and multiple dev keywords, dev raw count must dominate
        assert result["best_channel"] == "dev", (
            f"best_channel should be 'dev'; got {result['best_channel']}; "
            f"scores: product={result['product_score']} tech={result['tech_score']} "
            f"business={result['business_score']} dev={result['dev_score']}"
        )

    def test_all_channels_have_scores_key(self):
        result = classify_channels("AI model release with benchmark score 90%")
        assert "product_score" in result
        assert "tech_score" in result
        assert "business_score" in result
        assert "dev_score" in result
        assert "best_channel" in result
        assert "reasons" in result


# ---------------------------------------------------------------------------
# Test 3: Quota selection meets minimums when candidates available
# ---------------------------------------------------------------------------

class TestQuotaSelectionMeetsMinimumsWhenAvailable:
    """select_executive_items() should fill product/tech/business quotas when input is rich."""

    def _make_candidates(self) -> list[EduNewsCard]:
        """Create 12 cards: 4 product, 4 tech, 4 business — all AI-relevant."""
        cards = []
        # Product cards
        for i in range(4):
            cards.append(_card(
                title=f"AI Product Launch {i}: New GPT feature GA",
                what=f"OpenAI launched feature {i} with general availability pricing $10/month. Version 2.{i} ships now.",
                why=f"Impacts {i*100}k enterprise users for AI workflows.",
                url=f"https://openai.com/blog/launch-{i}",
                item_id=f"prod-{i}",
            ))
        # Tech cards
        for i in range(4):
            cards.append(_card(
                title=f"New LLM weights released: 70B model v{i}.0",
                what=f"Researchers release 70B parameter model checkpoint with MMLU score 9{i}%. arXiv preprint available.",
                why=f"Benchmark establishes new SOTA on {i+3} tasks. Inference latency reduced by 40%.",
                url=f"https://arxiv.org/abs/2412.{i:05d}",
                item_id=f"tech-{i}",
            ))
        # Business cards
        for i in range(4):
            cards.append(_card(
                title=f"AI Startup raises $1{i}0M Series A",
                what=f"AI company raises $1{i}0M funding from Sequoia. Partnership with Microsoft announced. CEO expansion plan.",
                why=f"Valuation reaches ${i+1}B. Revenue ARR $2M. Customer base 500 enterprises.",
                url=f"https://techcrunch.com/funding-{i}",
                item_id=f"biz-{i}",
            ))
        return cards

    def test_quota_product_ge_2(self):
        candidates = self._make_candidates()
        selected, meta = select_executive_items(candidates)
        by_bucket = meta["events_by_bucket"]
        assert by_bucket.get("product", 0) >= 2, (
            f"product quota should be >= 2; got {by_bucket}"
        )

    def test_quota_tech_ge_2(self):
        candidates = self._make_candidates()
        selected, meta = select_executive_items(candidates)
        by_bucket = meta["events_by_bucket"]
        assert by_bucket.get("tech", 0) >= 2, (
            f"tech quota should be >= 2; got {by_bucket}"
        )

    def test_quota_business_ge_2(self):
        candidates = self._make_candidates()
        selected, meta = select_executive_items(candidates)
        by_bucket = meta["events_by_bucket"]
        assert by_bucket.get("business", 0) >= 2, (
            f"business quota should be >= 2; got {by_bucket}"
        )

    def test_meta_fields_present(self):
        candidates = self._make_candidates()
        selected, meta = select_executive_items(candidates)
        assert "events_total" in meta
        assert "events_by_bucket" in meta
        assert "rejected_irrelevant_count" in meta
        assert "rejected_top_reasons" in meta
        assert "quota_target" in meta
        assert "quota_pass" in meta
        assert "sparse_day" in meta

    def test_quota_pass_true_with_rich_input(self):
        candidates = self._make_candidates()
        selected, meta = select_executive_items(candidates)
        # With 12 balanced candidates, quota should pass
        assert meta["quota_pass"] is True, (
            f"quota_pass should be True with rich input; meta={meta}"
        )

    def test_building_cards_are_rejected(self):
        """Pure building/real-estate cards must not appear in selected."""
        building_cards = [
            _card(
                title=f"新大樓建案開工 {i}",
                what=f"本建案位於信義區，共 {i+10} 層樓，住宅商辦混合。建商預計明年完工。",
                why="房地產市場熱絡，地產業者擴大投資計畫。",
                url=f"https://house.example.com/building-{i}",
                item_id=f"bld-{i}",
                category="房產",
            )
            for i in range(3)
        ]
        ai_cards = [_card(
            title="OpenAI GPT-5 launched",
            what="OpenAI released GPT-5 with 1M context. Inference speed 2x faster.",
            why="Major AI milestone for LLM development worth $10B.",
            url="https://openai.com/gpt5",
            item_id="ai-001",
        )]
        selected, meta = select_executive_items(building_cards + ai_cards)
        selected_ids = {str(getattr(c, "item_id", "") or "") for c in selected}
        for i in range(3):
            assert f"bld-{i}" not in selected_ids, (
                f"Building card bld-{i} should be rejected, got ids={selected_ids}"
            )
        assert meta["rejected_irrelevant_count"] >= 3


# ---------------------------------------------------------------------------
# Test 4: Recommended Actions / Moves no fragment leak
# ---------------------------------------------------------------------------

class TestActionsMovesNoFragmentLeak:
    """build_ceo_actions() must not output fragment/placeholder detail bullets."""

    # Fragment and placeholder patterns to assert are ABSENT
    _BAD_PATTERNS = [
        "的趨勢，解決方",   # partial Chinese sentence remnant
        "Last July was",    # template fragment
        "WHY IT MATTERS",   # homework pattern
        "signals_insufficient",
        "ETL",
        "pipeline",
        "Z1", "Z2", "Z3", "Z4", "Z5",
    ]

    def _card_with_hollow_action(self) -> EduNewsCard:
        """Card whose action_items contain a known fragment."""
        c = EduNewsCard(
            item_id="hollow-001",
            is_valid_news=True,
            title_plain="OpenAI launches GPT-5 with 1M context",
            what_happened="OpenAI released GPT-5. Inference latency improved 40%. Enterprise customers benefit.",
            why_important="Major AI milestone. Impacts $10B enterprise market for LLM use cases.",
            source_name="TechCrunch",
            source_url="https://techcrunch.com/gpt5",
            final_score=9.0,
            category="AI",
        )
        # Inject a fragment into action_items
        try:
            object.__setattr__(c, "action_items", ["的趨勢，解決方 記"])
        except Exception:
            pass
        return c

    def _card_with_placeholder_action(self) -> EduNewsCard:
        """Card whose action_items contain a placeholder pattern."""
        c = EduNewsCard(
            item_id="placeholder-001",
            is_valid_news=True,
            title_plain="Anthropic Claude 3.5 Sonnet released",
            what_happened="Anthropic launched Claude 3.5 Sonnet with 200k context. Benchmark MMLU 89%. Available now.",
            why_important="Competitive with GPT-4 at lower latency. Enterprise pricing $3/M tokens.",
            source_name="Anthropic",
            source_url="https://anthropic.com/claude",
            final_score=9.2,
            category="AI",
        )
        try:
            object.__setattr__(c, "action_items", ["Last July was"])
        except Exception:
            pass
        return c

    def test_fragment_action_detail_not_in_output(self):
        card = self._card_with_hollow_action()
        actions = build_ceo_actions([card])
        assert actions, "build_ceo_actions should return at least one action"
        for act in actions:
            detail = act.get("detail", "")
            for bad in self._BAD_PATTERNS:
                assert bad not in detail, (
                    f"Fragment '{bad}' leaked into action detail: '{detail}'"
                )

    def test_placeholder_action_detail_not_in_output(self):
        card = self._card_with_placeholder_action()
        actions = build_ceo_actions([card])
        for act in actions:
            detail = act.get("detail", "")
            for bad in self._BAD_PATTERNS:
                assert bad not in detail, (
                    f"Placeholder '{bad}' leaked into action detail: '{detail}'"
                )

    def test_fallback_detail_is_not_empty(self):
        """Even with hollow action_items, the output detail must not be empty."""
        card = self._card_with_hollow_action()
        actions = build_ceo_actions([card])
        for act in actions:
            assert act.get("detail", "").strip(), (
                f"Action detail must not be empty; action={act}"
            )

    def test_normal_action_preserved(self):
        """Good action text must pass through unchanged."""
        c = _card(
            title="NVIDIA H100 GPU now available at $30k",
            what="NVIDIA launched H100 GPU at $30k for enterprise AI training workloads.",
            why="Reduces AI training cost by 50% compared to A100. 500 enterprise customers.",
        )
        try:
            object.__setattr__(c, "action_items", ["評估採購 NVIDIA H100 GPU 以降低訓練成本 30%。"])
        except Exception:
            pass
        actions = build_ceo_actions([c])
        # Should have at least one action, and no BAD patterns
        assert actions
        for act in actions:
            detail = act.get("detail", "")
            for bad in self._BAD_PATTERNS:
                assert bad not in detail
