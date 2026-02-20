"""Tests for config/z0_sources.json business_72h and product_72h query coverage.

Verifies (offline, no network):
- business_72h query count >= 12
- product_72h query count >= 8
- At least N queries per tag include big-tech company names (to ensure high-signal
  commercial/product queries are present, not just generic keyword queries)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "z0_sources.json"
_BIG_TECH = frozenset({
    "openai", "microsoft", "nvidia", "google", "anthropic",
    "aws", "meta", "apple", "intel", "xai",
})


def _load_queries() -> list[dict]:
    return json.loads(_CONFIG_PATH.read_text(encoding="utf-8")).get("google_news_queries", [])


def _has_bigtech(q_str: str) -> bool:
    q_lower = q_str.lower()
    return any(name in q_lower for name in _BIG_TECH)


# ---------------------------------------------------------------------------
# business_72h
# ---------------------------------------------------------------------------

def test_business_72h_count():
    """Must have >= 12 business_72h queries to ensure broad commercial coverage."""
    queries = _load_queries()
    biz = [q for q in queries if q.get("tag") == "business_72h"]
    assert len(biz) >= 12, (
        f"Expected >= 12 business_72h queries, got {len(biz)}"
    )


def test_business_72h_big_tech_coverage():
    """At least 5 business_72h queries must reference a big-tech company by name."""
    queries = _load_queries()
    biz = [q for q in queries if q.get("tag") == "business_72h"]
    bigtech_count = sum(1 for q in biz if _has_bigtech(q.get("q", "")))
    assert bigtech_count >= 5, (
        f"Expected >= 5 business_72h queries with big-tech names, got {bigtech_count}. "
        f"Queries: {[q['q'][:60] for q in biz]}"
    )


def test_business_72h_covers_funding_and_earnings_topics():
    """business_72h queries must cover both funding/raising and earnings/revenue topics."""
    queries = _load_queries()
    biz_qs = [q.get("q", "").lower() for q in queries if q.get("tag") == "business_72h"]

    funding_hit  = any("fund" in q or "raised" in q or "series" in q for q in biz_qs)
    earnings_hit = any("earn" in q or "revenue" in q or "profit" in q for q in biz_qs)

    assert funding_hit,  "No business_72h query covers funding/raised/series topics"
    assert earnings_hit, "No business_72h query covers earnings/revenue/profit topics"


# ---------------------------------------------------------------------------
# product_72h
# ---------------------------------------------------------------------------

def test_product_72h_count():
    """Must have >= 8 product_72h queries to ensure broad product-launch coverage."""
    queries = _load_queries()
    prod = [q for q in queries if q.get("tag") == "product_72h"]
    assert len(prod) >= 8, (
        f"Expected >= 8 product_72h queries, got {len(prod)}"
    )


def test_product_72h_big_tech_coverage():
    """At least 4 product_72h queries must reference a big-tech company by name."""
    queries = _load_queries()
    prod = [q for q in queries if q.get("tag") == "product_72h"]
    bigtech_count = sum(1 for q in prod if _has_bigtech(q.get("q", "")))
    assert bigtech_count >= 4, (
        f"Expected >= 4 product_72h queries with big-tech names, got {bigtech_count}. "
        f"Queries: {[q['q'][:60] for q in prod]}"
    )


def test_product_72h_covers_launch_and_api_topics():
    """product_72h queries must cover launch/GA semantics and API/SDK keywords."""
    queries = _load_queries()
    prod_qs = [q.get("q", "").lower() for q in queries if q.get("tag") == "product_72h"]

    launch_hit = any(
        "launch" in q or "release" in q or "generally available" in q or " ga " in q
        for q in prod_qs
    )
    api_hit = any("api" in q or "sdk" in q or "beta" in q for q in prod_qs)

    assert launch_hit, "No product_72h query covers launch/release/GA semantics"
    assert api_hit,    "No product_72h query covers API/SDK/beta keywords"


# ---------------------------------------------------------------------------
# Combined big-tech query present
# ---------------------------------------------------------------------------

def test_combined_bigtech_product_query_exists():
    """At least one product_72h query must contain a combined big-tech OR group."""
    queries = _load_queries()
    prod = [q for q in queries if q.get("tag") == "product_72h"]
    # A combined query lists >= 5 big-tech names in one query string
    combined = [
        q for q in prod
        if sum(1 for name in _BIG_TECH if name in q.get("q", "").lower()) >= 5
    ]
    assert len(combined) >= 1, (
        "Expected at least one product_72h query with >= 5 big-tech names combined"
    )


def test_combined_bigtech_business_query_exists():
    """At least one business_72h query must contain a combined big-tech OR group."""
    queries = _load_queries()
    biz = [q for q in queries if q.get("tag") == "business_72h"]
    combined = [
        q for q in biz
        if sum(1 for name in _BIG_TECH if name in q.get("q", "").lower()) >= 5
    ]
    assert len(combined) >= 1, (
        "Expected at least one business_72h query with >= 5 big-tech names combined"
    )
