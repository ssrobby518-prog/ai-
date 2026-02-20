"""Pure-stdlib helper for building and writing acceptance_definitions.meta.json.

Provides two testable, side-effect-free functions that mirror the PowerShell
ConvertTo-Json logic in scripts/verify_online.ps1 — but callable offline from
Python tests without any online run or network access.

Usage (offline test):
    from utils.acceptance_definitions import build_acceptance_definitions, write_acceptance_definitions_meta
    payload = build_acceptance_definitions(
        run_head="abc123",
        collected_at="2026-02-20T00:00:00Z",
        z0_min_total_items=800,
        z0_min_frontier85_72h=10,
        kpi_min_events=6,
        kpi_min_product=2,
        kpi_min_tech=2,
        kpi_min_business=2,
        fail_fast_rules=[...],
    )
    write_acceptance_definitions_meta(tmp_path / "acceptance_definitions.meta.json", payload)
"""
from __future__ import annotations

import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Canonical fail-fast rules (mirrors verify_online.ps1 ACCEPTANCE DEFINITIONS)
# ---------------------------------------------------------------------------

DEFAULT_FAIL_FAST_RULES: list[str] = [
    "Z0 pool: actual < target (z0_pool_gates) => exit 1 before pipeline",
    "EXEC KPI: actual < target and sparse_day=false => exit 1 after pipeline",
    "verify_run.ps1: any of 9 gates fail => exit 1",
    "Z0 meta not found => exit 1",
    "delivery archive HEAD mismatch => exit 1",
]


def build_acceptance_definitions(
    run_head: str,
    collected_at: str | None,
    z0_min_total_items: int,
    z0_min_frontier85_72h: int,
    kpi_min_events: int,
    kpi_min_product: int,
    kpi_min_tech: int,
    kpi_min_business: int,
    fail_fast_rules: list[str] | None = None,
) -> dict:
    """Build the acceptance_definitions payload dict.

    The returned dict structure matches the JSON written by verify_online.ps1.
    All values are plain Python types — no external dependencies required.

    Args:
        run_head: git HEAD SHA at the time of the online run.
        collected_at: ISO-8601 timestamp from Z0 meta (or None when offline).
        z0_min_total_items: Z0 pool gate — minimum total items threshold.
        z0_min_frontier85_72h: Z0 pool gate — minimum frontier_ge_85_72h threshold.
        kpi_min_events: EXEC KPI gate — minimum total events.
        kpi_min_product: EXEC KPI gate — minimum product-bucket events.
        kpi_min_tech: EXEC KPI gate — minimum tech-bucket events.
        kpi_min_business: EXEC KPI gate — minimum business-bucket events.
        fail_fast_rules: List of rule strings; defaults to DEFAULT_FAIL_FAST_RULES.

    Returns:
        Ordered dict matching the acceptance_definitions.meta.json schema.
    """
    if fail_fast_rules is None:
        fail_fast_rules = list(DEFAULT_FAIL_FAST_RULES)

    return {
        "run_head": run_head,
        "collected_at": collected_at,
        "z0_pool_targets": {
            "min_total_items": int(z0_min_total_items),
            "min_frontier85_72h": int(z0_min_frontier85_72h),
        },
        "kpi_targets": {
            "events": int(kpi_min_events),
            "product": int(kpi_min_product),
            "tech": int(kpi_min_tech),
            "business": int(kpi_min_business),
        },
        "fail_fast_rules": [str(r) for r in fail_fast_rules],
    }


def write_acceptance_definitions_meta(path: "Path | str", payload: dict) -> None:
    """Serialise *payload* to UTF-8 JSON at *path* (parent dirs created automatically)."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
