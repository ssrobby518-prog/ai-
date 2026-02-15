from __future__ import annotations

from types import SimpleNamespace

from core.content_gate import apply_adaptive_content_gate, is_valid_article


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
    assert reason == "insufficient_sentences"


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
            body=(
                ("Platform migration completed across two regions with staged rollout safeguards. " * 8)
                + ("Operators validated recovery windows and latency targets. " * 8)
            )
        )
        for _ in range(4)
    ]
    kept, _rejected, stats = apply_adaptive_content_gate(items, min_keep_items=4)
    assert len(kept) == 4
    assert stats.level_used >= 2


def test_adaptive_gate_hard_reject_not_relaxed() -> None:
    items = [
        SimpleNamespace(body="This roundup lists links and quick updates. " * 40),
        SimpleNamespace(
            body=(
                ("Engineering teams completed migration planning with clear timelines. " * 12)
                + ("Customers confirmed readiness for controlled production rollout. " * 12)
            )
        ),
    ]
    kept, _rejected, stats = apply_adaptive_content_gate(items, min_keep_items=2)
    assert len(kept) == 1
    assert stats.rejected_by_reason.get("rejected_keyword:roundup", 0) == 1
