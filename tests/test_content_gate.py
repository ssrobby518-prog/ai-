from __future__ import annotations

from types import SimpleNamespace

from core.content_gate import apply_adaptive_content_gate, apply_split_content_gate, is_valid_article


def _long_sentence(seed: str, repeats: int = 120) -> str:
    return (seed + " ") * repeats


def test_rejects_short_content() -> None:
    ok, reason = is_valid_article("Too short.")
    assert ok is False
    assert reason == "content_too_short"


def test_rejects_residual_fragment() -> None:
    text = "Last July was... " + _long_sentence("partial fragment with no closing thought", repeats=80)
    ok, reason = is_valid_article(text)
    assert ok is False
    assert reason == "fragment_placeholder"


def test_rejects_roundup_keyword() -> None:
    text = (
        _long_sentence("This roundup collects product updates from multiple vendors.", repeats=40)
        + "The list includes links and quick notes for each update. "
        + _long_sentence("Teams can compare features and release timing across categories.", repeats=40)
    )
    ok, reason = is_valid_article(text)
    assert ok is False
    assert reason == "rejected_keyword:roundup"


def test_accepts_normal_article() -> None:
    text = (
        _long_sentence("The company announced a new production architecture for its inference cluster.", repeats=35)
        + "Engineers reported stable latency and a measurable reduction in operating cost across regions. "
        + _long_sentence("Partners validated the rollout plan with staged deployment and rollback safeguards.", repeats=35)
    )
    ok, reason = is_valid_article(text)
    assert ok is True
    assert reason is None


def test_adaptive_gate_relaxes_when_kept_too_low() -> None:
    items = [
        SimpleNamespace(
            title="AI inference update",
            body=(
                ("AI inference migration completed across two regions with staged rollout safeguards. " * 5)
                + ("GPU operators validated recovery windows and latency targets. " * 5)
            )
        )
        for _ in range(4)
    ]
    kept, _rejected, stats = apply_adaptive_content_gate(items, min_keep_items=4)
    assert len(kept) == 4
    assert stats.level_used >= 2
    assert stats.passed_strict < 4
    assert stats.passed_relaxed >= 1


def test_adaptive_gate_hard_reject_not_relaxed() -> None:
    items = [
        SimpleNamespace(title="AI roundup", body="This roundup lists links and quick AI updates. " * 40),
        SimpleNamespace(
            title="AI model update",
            body=(
                ("AI engineering teams completed inference migration planning with clear timelines. " * 12)
                + ("Customers confirmed readiness for controlled GPU production rollout. " * 12)
            )
        ),
    ]
    kept, _rejected, stats = apply_adaptive_content_gate(items, min_keep_items=2)
    assert len(kept) == 1
    assert stats.rejected_by_reason.get("rejected_keyword:roundup", 0) == 1


def test_split_gate_soft_passes_short_dense_signal_item() -> None:
    short_dense = (
        "OpenAI GPT-5.2 rollout for enterprise copilots reached 12 teams in 2026 with 35% latency gains. "
        "Microsoft Azure benchmark reported 90ms median response and $2.1M annual savings for production support. "
    )
    items = [
        SimpleNamespace(
            title="OpenAI enterprise rollout update",
            body=short_dense,
        ),
    ]

    event_candidates, signal_pool, rejected_map, stats = apply_split_content_gate(
        items,
        event_level=(1200, 3),
        signal_level=(300, 2),
    )

    assert len(event_candidates) == 0
    assert len(signal_pool) == 1
    assert stats.event_gate_pass_total == 0
    assert stats.signal_gate_pass_total == 1
    assert rejected_map == {}
    assert getattr(signal_pool[0], "gate_level", "") in {"signal", "signal_soft"}


def test_split_gate_promotes_fallback_signal_when_pool_empty() -> None:
    one_sentence_dense = (
        "OpenAI GPT-5.2 reached 12 enterprise teams in 2026, with Microsoft Azure reporting 35% latency gains and 90ms median response."
    )
    weak_item = "AI update available."

    items = [
        SimpleNamespace(title="Dense short update", body=one_sentence_dense),
        SimpleNamespace(title="Weak short update", body=weak_item),
    ]

    event_candidates, signal_pool, rejected_map, stats = apply_split_content_gate(
        items,
        event_level=(1200, 3),
        signal_level=(300, 2),
    )

    assert len(event_candidates) == 0
    assert stats.signal_gate_pass_total >= 1
    assert len(signal_pool) >= 1
    assert any(getattr(item, "gate_level", "") in {"signal_fallback", "signal_soft"} for item in signal_pool)
    assert 1 in rejected_map  # weak item remains rejected
