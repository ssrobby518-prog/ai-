"""Offline unit tests for Z0 frontier business_signal_bonus and product_release_bonus.

Tests verify:
  T1  Funding article triggers business_signal_bonus > 0
  T2  business_signal_bonus is capped at 25
  T3  Pricing article triggers both business and product bonus
  T4  product_release_bonus is capped at 20
  T5  Plain launch-only article triggers product bonus only
  T6  Near-miss article (no BigTech, no biz term) gets zero biz_bonus
  T7  Money amount pattern ($500M) triggers biz_term flag
  T8  Date-based version (2026.02 / R1) triggers prod_ver flag
  T9  Score clamp still applies (bonus stack never exceeds 100)
  T10 Old, unknown-platform, plain text stays below 85 after new bonuses
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.z0_collector import compute_frontier_score


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_item(
    title: str,
    summary: str = "",
    platform: str = "google_news",
    pub_hours_ago: float = 12.0,
    url: str = "https://example.com/item",
) -> dict:
    now = datetime.now(timezone.utc)
    pub = (now - timedelta(hours=pub_hours_ago)).isoformat()
    return {
        "title": title,
        "summary": summary,
        "url": url,
        "published_at": pub,
        "published_at_parsed": pub,
        "source": {
            "platform": platform,
            "feed_name": "test",
            "feed_url": "",
            "tag": "gnews",
        },
        "content_text": "",
        "collected_at": now.isoformat(),
    }


def _flags(item: dict) -> dict:
    """Return bonus flags stored on item after compute_frontier_score()."""
    return item.get("_bonus_flags", {})


# ---------------------------------------------------------------------------
# T1 — Funding article triggers business_signal_bonus > 0
# ---------------------------------------------------------------------------

def test_t1_funding_triggers_business_bonus() -> None:
    """T1: Article about OpenAI funding round must yield biz_bonus > 0."""
    item = _make_item(
        title="OpenAI raises $500 million Series B funding at $10 billion valuation",
        summary="OpenAI announced a new funding round led by Microsoft.",
        pub_hours_ago=12.0,
    )
    compute_frontier_score(item)
    f = _flags(item)
    assert f["biz_bonus"] > 0, f"Expected biz_bonus > 0; flags={f}"
    assert f["biz_bigtech"] is True, "Expected BigTech match for 'OpenAI'"
    assert f["biz_term"] is True, "Expected business term match (raises / funding / billion / valuation)"


# ---------------------------------------------------------------------------
# T2 — business_signal_bonus is capped at 25
# ---------------------------------------------------------------------------

def test_t2_business_bonus_capped_at_25() -> None:
    """T2: Even with multiple BigTech + money signals, biz_bonus <= 25."""
    item = _make_item(
        title="OpenAI Microsoft NVIDIA Google Anthropic AWS $10B $500M funding acquisition merger",
        summary="billion trillion revenue IPO unicorn valuation Series A Series B Series C",
    )
    compute_frontier_score(item)
    f = _flags(item)
    assert f["biz_bonus"] <= 25, f"biz_bonus exceeds cap 25: {f['biz_bonus']}"


# ---------------------------------------------------------------------------
# T3 — Pricing article triggers both bonuses
# ---------------------------------------------------------------------------

def test_t3_pricing_triggers_business_and_product_bonus() -> None:
    """T3: 'Anthropic announces new Claude API pricing' → both biz and prod bonus."""
    item = _make_item(
        title="Anthropic announces new Claude API pricing update for enterprise customers",
        summary="New API pricing tiers released today with SDK and platform changes.",
        pub_hours_ago=6.0,
    )
    compute_frontier_score(item)
    f = _flags(item)
    # Business: Anthropic (BigTech) + pricing (business term)
    assert f["biz_bonus"] > 0, f"Expected biz_bonus > 0; flags={f}"
    # Product: API + pricing (product launch terms)
    assert f["prod_bonus"] > 0, f"Expected prod_bonus > 0; flags={f}"


# ---------------------------------------------------------------------------
# T4 — product_release_bonus is capped at 20
# ---------------------------------------------------------------------------

def test_t4_product_bonus_capped_at_20() -> None:
    """T4: Even stacking launch + SDK + API + rollout + shipped, prod_bonus <= 20."""
    item = _make_item(
        title="v2026.02 launched released shipped rollout deploy API SDK update upgrade",
        summary="generally available new feature new model new version R1 R2",
    )
    compute_frontier_score(item)
    f = _flags(item)
    assert f["prod_bonus"] <= 20, f"prod_bonus exceeds cap 20: {f['prod_bonus']}"


# ---------------------------------------------------------------------------
# T5 — Launch-only article triggers product bonus, not necessarily business
# ---------------------------------------------------------------------------

def test_t5_launch_only_triggers_product_bonus() -> None:
    """T5: Plain launch article (no BigTech, no financial term) → prod_bonus > 0."""
    item = _make_item(
        title="New AI coding assistant launched with SDK and API access",
        summary="The product is generally available today. New features shipped.",
        platform="google_news",
        pub_hours_ago=8.0,
    )
    compute_frontier_score(item)
    f = _flags(item)
    assert f["prod_bonus"] > 0, f"Expected prod_bonus > 0; flags={f}"
    # biz_bonus may be 0 since no BigTech name and no specific financial term
    assert f["prod_launch"] is True, "Expected prod_launch flag to be set"


# ---------------------------------------------------------------------------
# T6 — No BigTech, no business term → biz_bonus = 0
# ---------------------------------------------------------------------------

def test_t6_no_bigtech_no_term_zero_biz_bonus() -> None:
    """T6: Article with no BigTech name and no financial term → biz_bonus = 0."""
    item = _make_item(
        title="Interesting discussion about neural networks and transformers",
        summary="Researchers study attention mechanisms in deep learning models.",
        platform="reddit",
        pub_hours_ago=5.0,
    )
    compute_frontier_score(item)
    f = _flags(item)
    assert f["biz_bonus"] == 0, f"Expected biz_bonus == 0; flags={f}"


# ---------------------------------------------------------------------------
# T7 — Money pattern ($500M) triggers biz_term flag
# ---------------------------------------------------------------------------

def test_t7_money_amount_triggers_biz_term() -> None:
    """T7: '$500M' pattern alone must set biz_term = True."""
    item = _make_item(
        title="Startup closes $500M round for AI chip development",
        summary="The company raised $500M in its latest financing.",
        pub_hours_ago=10.0,
    )
    compute_frontier_score(item)
    f = _flags(item)
    assert f["biz_term"] is True, f"Expected biz_term True from $500M; flags={f}"
    assert f["biz_bonus"] > 0, f"Expected biz_bonus > 0; flags={f}"


# ---------------------------------------------------------------------------
# T8 — Date-based version triggers prod_ver flag
# ---------------------------------------------------------------------------

def test_t8_date_version_triggers_prod_ver() -> None:
    """T8: '2026.02' OR 'R1' must set prod_ver = True."""
    item_date = _make_item(
        title="AI platform 2026.02 release now generally available",
        pub_hours_ago=4.0,
    )
    compute_frontier_score(item_date)
    assert _flags(item_date)["prod_ver"] is True, "Expected prod_ver True for '2026.02'"

    item_r1 = _make_item(
        title="Model R1 released with major capability improvements",
        pub_hours_ago=4.0,
    )
    compute_frontier_score(item_r1)
    assert _flags(item_r1)["prod_ver"] is True, "Expected prod_ver True for 'R1'"


# ---------------------------------------------------------------------------
# T9 — Final score clamp at 100 with all bonuses stacked
# ---------------------------------------------------------------------------

def test_t9_score_clamped_at_100_with_bonuses() -> None:
    """T9: Even with all bonuses active, score must not exceed 100."""
    item = _make_item(
        title="OpenAI Microsoft NVIDIA raise $10B Series B; GPT-5 v2.0.0 released API SDK",
        summary="arXiv:2402.10055 MoE 70B params MMLU 99% benchmark. Open-source weights released.",
        url="https://arxiv.org/abs/2402.10055",
        platform="openai",
        pub_hours_ago=1.0,
    )
    score = compute_frontier_score(item)
    assert score == 100, f"Expected score clamped to 100, got {score}"
    f = _flags(item)
    assert f["biz_bonus"] == 25, f"Expected biz_bonus at cap 25; flags={f}"
    assert f["prod_bonus"] == 20, f"Expected prod_bonus at cap 20; flags={f}"


# ---------------------------------------------------------------------------
# T10 — Old, plain, unknown-platform item stays below 85 after new bonuses
# ---------------------------------------------------------------------------

def test_t10_old_plain_item_stays_below_85() -> None:
    """T10: Old community post with 'pricing' in title must stay < 85."""
    item = _make_item(
        title="Interesting discussion about AI pricing strategies",
        summary="People are talking about the cost of AI tools.",
        platform="unknown",
        pub_hours_ago=200.0,
    )
    score = compute_frontier_score(item)
    assert score < 85, f"Expected < 85 for old/plain item with only 'pricing'; got {score}"
