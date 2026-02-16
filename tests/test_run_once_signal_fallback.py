from __future__ import annotations

from scripts.run_once import _select_processing_items
from schemas.models import RawItem


def _raw(item_id: str) -> RawItem:
    return RawItem(
        item_id=item_id,
        title=f"title-{item_id}",
        url=f"https://example.com/{item_id}",
        body="OpenAI GPT-5 rollout reached 12 teams with 35% latency reduction. Microsoft confirmed benchmarks.",
        published_at="2026-02-16T00:00:00+00:00",
        source_name="test",
        source_category="tech",
        lang="en",
    )


def test_select_processing_items_prefers_filtered_items() -> None:
    filtered = [_raw("f1"), _raw("f2")]
    signal_pool = [_raw("s1")]

    selected, used_fallback = _select_processing_items(filtered, signal_pool, fallback_limit=3)

    assert used_fallback is False
    assert [x.item_id for x in selected] == ["f1", "f2"]


def test_select_processing_items_uses_signal_pool_when_event_empty() -> None:
    filtered: list[RawItem] = []
    signal_pool = [_raw("s1"), _raw("s2"), _raw("s3"), _raw("s4")]

    selected, used_fallback = _select_processing_items(filtered, signal_pool, fallback_limit=2)

    assert used_fallback is True
    assert [x.item_id for x in selected] == ["s1", "s2"]
    assert bool(getattr(selected[0], "signal_gate_pass", False)) is True
    assert bool(getattr(selected[0], "event_gate_pass", True)) is False
    assert bool(getattr(selected[0], "low_confidence", False)) is True


def test_select_processing_items_adds_signal_context_when_event_exists() -> None:
    filtered = [_raw("f1")]
    signal_pool = [_raw("f1"), _raw("s2"), _raw("s3")]

    selected, used_fallback = _select_processing_items(
        filtered,
        signal_pool,
        fallback_limit=3,
        include_signal_context=True,
        signal_context_limit=2,
    )

    assert used_fallback is False
    assert [x.item_id for x in selected] == ["f1", "s2", "s3"]
    assert bool(getattr(selected[1], "signal_gate_pass", False)) is True
    assert bool(getattr(selected[1], "event_gate_pass", True)) is False
    assert bool(getattr(selected[1], "low_confidence", False)) is True


def test_select_processing_items_empty_when_no_candidates() -> None:
    selected, used_fallback = _select_processing_items([], [], fallback_limit=3)
    assert selected == []
    assert used_fallback is False
