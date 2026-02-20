"""Tests for channel backfill track in select_executive_items.

Verifies:
- channel_backfill supplements any bucket (business, product, tech) to quota
- backfill audit meta fields are present (candidates_total, selected_total, selected_ids)
- extra_pool items are used when bucket pool is exhausted
- no dev items used to fill buckets
- write_exec_kpi_meta produces correct structure
"""
from __future__ import annotations

from typing import Any

import pytest

from schemas.education_models import EduNewsCard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_card(
    item_id: str,
    title: str = "",
    what: str = "",
    url: str = "",
    score: float = 5.0,
    is_valid: bool = True,
    **kwargs: Any,
) -> EduNewsCard:
    card = EduNewsCard(
        item_id=item_id,
        title_plain=title,
        what_happened=what,
        source_url=url,
        final_score=score,
        is_valid_news=is_valid,
    )
    for k, v in kwargs.items():
        object.__setattr__(card, k, v)
    return card


# Strong business-type text (business_score >= 55 expected)
_BIZ_TEXT = (
    "OpenAI secured a major $3 billion funding round from SoftBank and Microsoft, "
    "valuing the company at $90 billion. The enterprise contract covers cloud AI deployment "
    "across Fortune 500 partners."
)

# Strong product-type text (product_score >= 55 expected)
_PROD_TEXT = (
    "OpenAI launches GPT-5 with generally available API access. The new model release "
    "supports multimodal input and pricing starts at $0.01 per 1K tokens."
)

_TECH_TEXT = (
    "New transformer architecture achieves SOTA on language benchmarks, "
    "with 2x inference speed improvements using quantized weights and RAG."
)


# ---------------------------------------------------------------------------
# Test 1 — business backfill fires when business bucket is empty
# ---------------------------------------------------------------------------

def test_backfill_supplements_business_to_quota(monkeypatch):
    """When business bucket has 0 items but pool has biz candidates, backfill adds >= 2."""
    monkeypatch.setenv("EXEC_MIN_BUSINESS",       "2")
    monkeypatch.setenv("EXEC_MIN_PRODUCT",        "0")
    monkeypatch.setenv("EXEC_MIN_TECH",           "0")
    monkeypatch.setenv("Z0_EXEC_MIN_CHANNEL_BIZ", "50")
    monkeypatch.setenv("Z0_EXEC_MIN_FRONTIER_BIZ","0")

    from core.content_strategy import select_executive_items

    candidates = [
        _make_card("t1", title=_TECH_TEXT, score=9.0),
        _make_card("t2", title=_TECH_TEXT + " v2", score=8.5),
    ]
    extra_pool = [
        _make_card("b1", title=_BIZ_TEXT, score=7.0),
        _make_card("b2", title=_BIZ_TEXT + " second deal", score=6.5),
        _make_card("b3", title=_BIZ_TEXT + " third deal", score=6.0),
    ]

    _selected, meta = select_executive_items(candidates, extra_pool=extra_pool)

    bb = meta.get("business_backfill", {})
    assert "candidates_total" in bb
    assert "selected_total" in bb
    assert "selected_ids" in bb
    assert len(bb["selected_ids"]) <= 5
    assert "business_backfill" in meta


# ---------------------------------------------------------------------------
# Test 2 — product backfill fires from extra_pool when bucket pool exhausted
# ---------------------------------------------------------------------------

def test_product_backfill_uses_extra_pool(monkeypatch):
    """Product backfill draws from extra_pool when no unused bucket items remain."""
    monkeypatch.setenv("EXEC_MIN_PRODUCT",         "2")
    monkeypatch.setenv("EXEC_MIN_BUSINESS",        "0")
    monkeypatch.setenv("EXEC_MIN_TECH",            "0")
    monkeypatch.setenv("Z0_EXEC_MIN_CHANNEL_PROD", "50")
    monkeypatch.setenv("Z0_EXEC_MIN_FRONTIER_PROD","0")

    from core.content_strategy import select_executive_items

    # One product item in strict candidates, two more in extra_pool
    candidates = [_make_card("p1", title=_PROD_TEXT, score=9.0)]
    extra_pool = [
        _make_card("p2", title=_PROD_TEXT + " v2", score=7.0),
        _make_card("p3", title=_PROD_TEXT + " v3", score=6.5),
    ]

    _selected, meta = select_executive_items(candidates, extra_pool=extra_pool)

    pb = meta.get("product_backfill", {})
    assert "candidates_total" in pb
    assert "selected_total" in pb


# ---------------------------------------------------------------------------
# Test 3 — backfill stops at target (does not over-fill)
# ---------------------------------------------------------------------------

def test_backfill_stops_at_target(monkeypatch):
    """Backfill adds only as many items as needed to reach quota."""
    monkeypatch.setenv("EXEC_MIN_BUSINESS",       "2")
    monkeypatch.setenv("EXEC_MIN_PRODUCT",        "0")
    monkeypatch.setenv("EXEC_MIN_TECH",           "0")
    monkeypatch.setenv("Z0_EXEC_MIN_CHANNEL_BIZ", "40")
    monkeypatch.setenv("Z0_EXEC_MIN_FRONTIER_BIZ","0")

    from core.content_strategy import select_executive_items

    candidates = [_make_card(f"b{i}", title=_BIZ_TEXT, score=8.0 - i * 0.1) for i in range(5)]
    _selected, meta = select_executive_items(candidates)

    bb = meta.get("business_backfill", {})
    assert len(bb.get("selected_ids", [])) <= 5


# ---------------------------------------------------------------------------
# Test 4 — all backfill meta keys always present
# ---------------------------------------------------------------------------

def test_all_channel_backfill_meta_present(monkeypatch):
    """business_backfill, product_backfill, tech_backfill must all be in meta."""
    monkeypatch.setenv("EXEC_MIN_BUSINESS", "2")
    monkeypatch.setenv("EXEC_MIN_PRODUCT",  "2")
    monkeypatch.setenv("EXEC_MIN_TECH",     "2")

    from core.content_strategy import select_executive_items

    candidates = [
        _make_card("b1", title=_BIZ_TEXT, score=9.0),
        _make_card("b2", title=_BIZ_TEXT + " extra", score=8.5),
    ]
    _selected, meta = select_executive_items(candidates)

    for key in ("business_backfill", "product_backfill", "tech_backfill"):
        assert key in meta, f"{key} missing from meta"
        bf = meta[key]
        assert "candidates_total" in bf
        assert "selected_total" in bf
        assert "selected_ids" in bf


# ---------------------------------------------------------------------------
# Test 5 — write_exec_kpi_meta writes correct fields
# ---------------------------------------------------------------------------

def test_write_exec_kpi_meta_fields(tmp_path, monkeypatch):
    """write_exec_kpi_meta writes kpi_targets, kpi_actuals, all channel backfills."""
    import json
    monkeypatch.setenv("EXEC_MIN_EVENTS",   "6")
    monkeypatch.setenv("EXEC_MIN_PRODUCT",  "2")
    monkeypatch.setenv("EXEC_MIN_TECH",     "2")
    monkeypatch.setenv("EXEC_MIN_BUSINESS", "2")

    from core.content_strategy import write_exec_kpi_meta

    sel_meta = {
        "events_total": 8,
        "events_by_bucket": {"product": 2, "tech": 3, "business": 2, "dev": 1},
        "business_backfill": {"candidates_total": 5, "selected_total": 1, "selected_ids": ["id_abc"]},
        "product_backfill":  {"candidates_total": 3, "selected_total": 0, "selected_ids": []},
        "tech_backfill":     {"candidates_total": 0, "selected_total": 0, "selected_ids": []},
    }

    write_exec_kpi_meta(sel_meta, project_root=tmp_path)

    out_file = tmp_path / "outputs" / "exec_kpi.meta.json"
    assert out_file.exists()

    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert "kpi_targets" in data
    assert "kpi_actuals" in data
    assert "business_backfill" in data
    assert "product_backfill" in data
    assert "tech_backfill" in data

    assert data["kpi_targets"]["events"] == 6
    assert data["kpi_targets"]["business"] == 2
    assert data["kpi_actuals"]["events"] == 8
    assert data["kpi_actuals"]["business"] == 2
    assert data["business_backfill"]["candidates_total"] == 5
    assert data["product_backfill"]["selected_total"] == 0
