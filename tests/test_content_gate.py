from __future__ import annotations

from core.content_gate import is_valid_article


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
