"""Data schemas for the pipeline (A, B, C) with validation helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Schema A  –  extraction + summary
# ---------------------------------------------------------------------------


@dataclass
class SchemaA:
    item_id: str = ""
    title_zh: str = ""
    summary_zh: str = ""
    category: str = ""
    category_confidence: float = 0.0
    entities: list[str] = field(default_factory=list)
    key_points: list[str] = field(default_factory=list)
    source_id: str = ""
    score_seed: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> SchemaA:
        return cls(
            item_id=str(d.get("item_id", "")),
            title_zh=str(d.get("title_zh", "")),
            summary_zh=str(d.get("summary_zh", "")),
            category=str(d.get("category", "")),
            category_confidence=float(d.get("category_confidence", 0)),
            entities=list(d.get("entities") or []),
            key_points=list(d.get("key_points") or []),
            source_id=str(d.get("source_id", "")),
            score_seed=float(d.get("score_seed", 0)),
        )


# ---------------------------------------------------------------------------
# Schema B  –  scoring
# ---------------------------------------------------------------------------


@dataclass
class SchemaB:
    item_id: str = ""
    novelty: float = 0.0
    utility: float = 0.0
    heat: float = 0.0
    feasibility: float = 0.0
    final_score: float = 0.0
    dup_risk: float = 0.0
    is_ad: bool = False
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> SchemaB:
        return cls(
            item_id=str(d.get("item_id", "")),
            novelty=float(d.get("novelty", 0)),
            utility=float(d.get("utility", 0)),
            heat=float(d.get("heat", 0)),
            feasibility=float(d.get("feasibility", 0)),
            final_score=float(d.get("final_score", 0)),
            dup_risk=float(d.get("dup_risk", 0)),
            is_ad=bool(d.get("is_ad", False)),
            tags=list(d.get("tags") or []),
        )


# ---------------------------------------------------------------------------
# Schema C  –  Feishu card
# ---------------------------------------------------------------------------


@dataclass
class SchemaC:
    item_id: str = ""
    card_md: str = ""
    title: str = ""
    brief: str = ""
    cta_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> SchemaC:
        return cls(
            item_id=str(d.get("item_id", "")),
            card_md=str(d.get("card_md", "")),
            title=str(d.get("title", "")),
            brief=str(d.get("brief", "")),
            cta_url=str(d.get("cta_url", "")),
        )


# ---------------------------------------------------------------------------
# Merged result
# ---------------------------------------------------------------------------


@dataclass
class MergedResult:
    item_id: str
    schema_a: SchemaA
    schema_b: SchemaB
    schema_c: SchemaC
    passed_gate: bool = False

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "schema_a": self.schema_a.to_dict(),
            "schema_b": self.schema_b.to_dict(),
            "schema_c": self.schema_c.to_dict(),
            "passed_gate": self.passed_gate,
        }


# ---------------------------------------------------------------------------
# Deep Analysis (Z4)
# ---------------------------------------------------------------------------


@dataclass
class ItemDeepDive:
    item_id: str = ""
    # Event breakdown
    core_facts: list[str] = field(default_factory=list)
    evidence_excerpts: list[str] = field(default_factory=list)
    event_breakdown: str = ""
    # Forces & incentives
    forces_incentives: str = ""
    # First principles
    first_principles_mechanism: str = ""  # chosen mechanism from controlled list
    first_principles: str = ""
    # Second-order effects (split)
    derivable_effects: list[str] = field(default_factory=list)
    speculative_effects: list[str] = field(default_factory=list)
    second_order_effects: str = ""  # kept for backward compat
    # Opportunities (max 3, each tied to mechanism + stakeholder)
    opportunities: list[str] = field(default_factory=list)
    # Strategic outlook
    observation_metrics: list[str] = field(default_factory=list)
    counter_risks: list[str] = field(default_factory=list)
    strategic_outlook_3y: str = ""
    # Signal
    signal_strength: float = 0.0
    evidence_density: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DeepAnalysisReport:
    generated_at: str = ""
    total_items: int = 0
    executive_meta_signals: str = ""
    per_item_analysis: list[ItemDeepDive] = field(default_factory=list)
    emerging_macro_themes: str = ""
    opportunity_map: str = ""
    actionable_signals: str = ""

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "total_items": self.total_items,
            "executive_meta_signals": self.executive_meta_signals,
            "per_item_analysis": [item.to_dict() for item in self.per_item_analysis],
            "emerging_macro_themes": self.emerging_macro_themes,
            "opportunity_map": self.opportunity_map,
            "actionable_signals": self.actionable_signals,
        }


# ---------------------------------------------------------------------------
# Raw item (normalized from RSS)
# ---------------------------------------------------------------------------


@dataclass
class RawItem:
    item_id: str = ""
    title: str = ""
    url: str = ""
    body: str = ""
    published_at: str = ""  # ISO8601 UTC
    source_name: str = ""
    source_category: str = ""
    lang: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def validate_json_schema(data: dict, schema_cls: type[Any]) -> bool:
    fields = getattr(schema_cls, "__dataclass_fields__", None)
    if not isinstance(fields, dict):
        return False
    expected = set(fields.keys())
    return expected.issubset(data.keys())
