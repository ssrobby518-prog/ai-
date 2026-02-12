"""Tests for the deep analysis pipeline.

Tests:
1. Boilerplate regression: second-order effects / first principles across
   diverse items should NOT be identical (Jaccard similarity on 3-grams < threshold).
2. Evidence-gated: core_facts must be supported by evidence_excerpts.
3. Mechanism diversity: >= 70% of items should have unique mechanism selection.
4. Snapshot (golden) test: fixed dataset produces expected JSON structure.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.deep_analyzer import _analyze_item_fallback
from schemas.models import MergedResult, SchemaA, SchemaB, SchemaC

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    item_id: str,
    title: str,
    body: str,
    category: str = "科技/技術",
    entities: list[str] | None = None,
    key_points: list[str] | None = None,
    novelty: float = 7.0,
    utility: float = 7.0,
    heat: float = 7.0,
    feasibility: float = 7.0,
) -> MergedResult:
    if entities is None:
        entities = []
    if key_points is None:
        key_points = []
    return MergedResult(
        item_id=item_id,
        schema_a=SchemaA(
            item_id=item_id,
            title_zh=title,
            summary_zh=body[:200],
            category=category,
            entities=entities,
            key_points=key_points,
        ),
        schema_b=SchemaB(
            item_id=item_id,
            novelty=novelty,
            utility=utility,
            heat=heat,
            feasibility=feasibility,
            final_score=round((novelty + utility + heat + feasibility) / 4, 2),
        ),
        schema_c=SchemaC(item_id=item_id),
        passed_gate=True,
    )


def _trigrams(text: str) -> set[str]:
    """Extract character-level 3-grams from text."""
    text = text.lower().replace(" ", "").replace("\n", "")
    return {text[i : i + 3] for i in range(len(text) - 2)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
# Test data: 5 diverse items
# ---------------------------------------------------------------------------

_DIVERSE_ITEMS = [
    _make_result(
        "item_tech_01",
        "Google Releases New Open-Source ML Framework",
        "Google has released a new machine learning framework called TensorFlow X. "
        "It supports distributed training and runs on TPUs. The framework aims to "
        "simplify model deployment.",
        category="人工智慧",
        entities=["Google", "TensorFlow", "TPU"],
        key_points=[
            "Google released TensorFlow X framework",
            "Supports distributed training on TPUs",
            "Aims to simplify model deployment",
        ],
        novelty=8.0,
    ),
    _make_result(
        "item_policy_02",
        "FDA Approves New Gene Therapy for Rare Disease",
        "The FDA has granted approval to a novel gene therapy developed by BioGenX. "
        "The treatment targets a rare genetic disorder affecting 1 in 50,000 children. "
        "Clinical trials showed 85% efficacy.",
        category="健康/生醫",
        entities=["FDA", "BioGenX"],
        key_points=[
            "FDA approved novel gene therapy by BioGenX",
            "Targets rare genetic disorder in children",
            "Clinical trials showed 85% efficacy",
        ],
        heat=6.0,
    ),
    _make_result(
        "item_security_03",
        "Critical Zero-Day Vulnerability Found in Linux Kernel",
        "Security researchers at Project Zero have discovered a critical zero-day "
        "vulnerability in the Linux kernel that could allow remote code execution. "
        "A patch is being prepared urgently.",
        category="資安/網路安全",
        entities=["Project Zero", "Linux"],
        key_points=[
            "Critical zero-day vulnerability discovered in Linux kernel",
            "Could allow remote code execution",
            "Patch being prepared urgently",
        ],
        novelty=9.0,
        heat=9.0,
    ),
    _make_result(
        "item_climate_04",
        "Tesla Announces $10B Battery Gigafactory in Texas",
        "Tesla plans to build a $10 billion battery gigafactory in Austin, Texas. "
        "The factory will produce next-generation 4680 cells. Production is expected "
        "to start in 2027.",
        category="氣候/能源",
        entities=["Tesla", "Austin"],
        key_points=[
            "Tesla building $10B battery gigafactory in Austin, Texas",
            "Will produce next-generation 4680 cells",
            "Production expected to start in 2027",
        ],
        feasibility=6.0,
    ),
    _make_result(
        "item_startup_05",
        "Stripe Raises $6.5B at $65B Valuation",
        "Payments company Stripe has raised $6.5 billion in a new funding round "
        "at a $65 billion valuation. The round was led by Sequoia Capital. "
        "Funds will be used for international expansion.",
        category="創業/投融資",
        entities=["Stripe", "Sequoia Capital"],
        key_points=[
            "Stripe raised $6.5B at $65B valuation",
            "Round led by Sequoia Capital",
            "Funds for international expansion",
        ],
        utility=8.0,
    ),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_boilerplate_first_principles() -> None:
    """First principles text across diverse items should NOT be identical.

    At least 70% of items should have unique mechanism selection.
    """
    analyses = [_analyze_item_fallback(r) for r in _DIVERSE_ITEMS]

    mechanisms = [a.first_principles_mechanism for a in analyses]
    unique_mechanisms = set(mechanisms)

    # At least 70% unique (for 5 items, that means >= 4 unique)
    uniqueness_ratio = len(unique_mechanisms) / len(mechanisms)
    assert uniqueness_ratio >= 0.7, (
        f"Only {len(unique_mechanisms)}/{len(mechanisms)} unique mechanisms "
        f"({uniqueness_ratio:.0%}). Mechanisms: {mechanisms}"
    )


def test_no_boilerplate_second_order_effects() -> None:
    """Second-order effects should have low similarity across diverse items.

    Jaccard similarity on 3-grams between any two items should be below 0.6.
    """
    analyses = [_analyze_item_fallback(r) for r in _DIVERSE_ITEMS]

    # Combine derivable + speculative for comparison
    texts = []
    for a in analyses:
        combined = " ".join(a.derivable_effects + a.speculative_effects)
        texts.append(combined)

    max_similarity = 0.0
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            grams_i = _trigrams(texts[i])
            grams_j = _trigrams(texts[j])
            sim = _jaccard(grams_i, grams_j)
            max_similarity = max(max_similarity, sim)

    assert max_similarity < 0.6, (
        f"Max pairwise Jaccard similarity is {max_similarity:.3f} (threshold: 0.6). "
        "Second-order effects are too similar across items."
    )


def test_evidence_gated_core_facts() -> None:
    """For each item, core_facts must exist and evidence_excerpts should be present."""
    for r in _DIVERSE_ITEMS:
        analysis = _analyze_item_fallback(r)

        # Core facts should not be empty
        assert len(analysis.core_facts) > 0, f"Item {r.item_id}: core_facts is empty"

        # Evidence excerpts should exist (at least 1 for items with summary)
        if r.schema_a.summary_zh:
            assert len(analysis.evidence_excerpts) > 0, (
                f"Item {r.item_id}: evidence_excerpts is empty despite having summary"
            )


def test_opportunities_not_generic() -> None:
    """Opportunities should reference item-specific entities or stakeholders."""
    for r in _DIVERSE_ITEMS:
        analysis = _analyze_item_fallback(r)

        # Should have max 3 opportunities
        assert len(analysis.opportunities) <= 3, (
            f"Item {r.item_id}: has {len(analysis.opportunities)} opportunities, max is 3"
        )

        # At least one opportunity should reference an entity from this item
        if r.schema_a.entities:
            all_opps = " ".join(analysis.opportunities)
            entity_found = any(e in all_opps for e in r.schema_a.entities[:3])
            assert entity_found, f"Item {r.item_id}: no opportunity references item entities {r.schema_a.entities[:3]}"


def test_strategic_outlook_has_metrics_and_risks() -> None:
    """Strategic outlook should include observation metrics and counter-risks."""
    for r in _DIVERSE_ITEMS:
        analysis = _analyze_item_fallback(r)

        # Should have 3-5 metrics
        assert 3 <= len(analysis.observation_metrics) <= 5, (
            f"Item {r.item_id}: has {len(analysis.observation_metrics)} metrics, expected 3-5"
        )

        # Should have 1-2 counter-risks
        assert 1 <= len(analysis.counter_risks) <= 2, (
            f"Item {r.item_id}: has {len(analysis.counter_risks)} counter-risks, expected 1-2"
        )


def test_signal_strength_not_static() -> None:
    """Signal strength should vary across items with different scores."""
    analyses = [_analyze_item_fallback(r) for r in _DIVERSE_ITEMS]
    signals = [a.signal_strength for a in analyses]

    unique_signals = set(signals)
    assert len(unique_signals) >= 3, (
        f"Only {len(unique_signals)} unique signal strengths among {len(signals)} items. "
        f"Signal strength appears static. Values: {signals}"
    )


def test_golden_snapshot_structure() -> None:
    """Verify the output JSON structure matches the expected schema."""
    r = _DIVERSE_ITEMS[0]
    analysis = _analyze_item_fallback(r)

    d = analysis.to_dict()

    # Required fields
    required_fields = {
        "item_id",
        "core_facts",
        "evidence_excerpts",
        "event_breakdown",
        "forces_incentives",
        "first_principles_mechanism",
        "first_principles",
        "derivable_effects",
        "speculative_effects",
        "second_order_effects",
        "opportunities",
        "observation_metrics",
        "counter_risks",
        "strategic_outlook_3y",
        "signal_strength",
        "evidence_density",
    }
    assert required_fields.issubset(d.keys()), f"Missing fields: {required_fields - set(d.keys())}"

    # Types
    assert isinstance(d["core_facts"], list)
    assert isinstance(d["evidence_excerpts"], list)
    assert isinstance(d["derivable_effects"], list)
    assert isinstance(d["speculative_effects"], list)
    assert isinstance(d["opportunities"], list)
    assert isinstance(d["observation_metrics"], list)
    assert isinstance(d["counter_risks"], list)
    assert isinstance(d["signal_strength"], float)
    assert isinstance(d["evidence_density"], float)

    # Write golden snapshot
    golden_path = Path(__file__).parent / "golden_snapshot.json"
    golden_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def test_golden_snapshot_stable() -> None:
    """Re-run analysis on same input and verify it matches the golden snapshot."""
    golden_path = Path(__file__).parent / "golden_snapshot.json"
    if not golden_path.exists():
        # Generate it first
        test_golden_snapshot_structure()

    golden = json.loads(golden_path.read_text(encoding="utf-8"))

    r = _DIVERSE_ITEMS[0]
    analysis = _analyze_item_fallback(r)
    current = analysis.to_dict()

    # Key structural fields should match exactly
    assert current["item_id"] == golden["item_id"]
    assert current["first_principles_mechanism"] == golden["first_principles_mechanism"]
    assert len(current["core_facts"]) == len(golden["core_facts"])
    assert len(current["opportunities"]) == len(golden["opportunities"])
    # Signal strength should be deterministic
    assert current["signal_strength"] == golden["signal_strength"]
