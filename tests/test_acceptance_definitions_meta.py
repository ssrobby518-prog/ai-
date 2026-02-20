"""Offline-first tests for acceptance_definitions.meta.json contract.

All three tests ALWAYS RUN — never skipped.  They use
utils/acceptance_definitions.build_acceptance_definitions() to produce a
canonical payload offline, then validate the schema contract.

T1  test_acceptance_meta_offline_writer_fields_present
      — build payload → assert all required fields + types + values > 0

T2  test_acceptance_meta_offline_writer_roundtrip_valid_json
      — write to tmp_path → json.load → verify structure round-trips cleanly

T3  test_acceptance_meta_real_outputs_if_present_else_offline_only
      — if outputs/acceptance_definitions.meta.json exists on disk: validate it
      — else: validate offline-built payload (no skip, assert True branch noted)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.acceptance_definitions import (
    DEFAULT_FAIL_FAST_RULES,
    build_acceptance_definitions,
    write_acceptance_definitions_meta,
)

# Path to the real runtime meta (written by verify_online.ps1)
_REAL_META_PATH = Path(__file__).resolve().parent.parent / "outputs" / "acceptance_definitions.meta.json"

# Canonical test payload (covers all spec fields)
_SAMPLE_PAYLOAD = build_acceptance_definitions(
    run_head="deadbeef" * 5,
    collected_at="2026-02-20T00:00:00Z",
    z0_min_total_items=800,
    z0_min_frontier85_72h=10,
    kpi_min_events=6,
    kpi_min_product=2,
    kpi_min_tech=2,
    kpi_min_business=2,
)


def _validate_payload(payload: dict) -> None:
    """Shared contract assertions — called by all three tests."""
    # Top-level keys present
    for key in ("run_head", "collected_at", "z0_pool_targets", "kpi_targets", "fail_fast_rules"):
        assert key in payload, f"Missing top-level key: {key!r}"

    # z0_pool_targets: int fields > 0
    z0t = payload["z0_pool_targets"]
    assert "min_total_items"    in z0t, "z0_pool_targets missing min_total_items"
    assert "min_frontier85_72h" in z0t, "z0_pool_targets missing min_frontier85_72h"
    assert isinstance(z0t["min_total_items"],    int), "min_total_items must be int"
    assert isinstance(z0t["min_frontier85_72h"], int), "min_frontier85_72h must be int"
    assert z0t["min_total_items"]    > 0, "min_total_items must be > 0"
    assert z0t["min_frontier85_72h"] > 0, "min_frontier85_72h must be > 0"

    # kpi_targets: all four int fields > 0
    kpi = payload["kpi_targets"]
    for field in ("events", "product", "tech", "business"):
        assert field in kpi,             f"kpi_targets missing {field!r}"
        assert isinstance(kpi[field], int), f"kpi_targets.{field} must be int"
        assert kpi[field] > 0,           f"kpi_targets.{field} must be > 0"

    # fail_fast_rules: non-empty list[str] with "actual < target" semantics
    rules = payload["fail_fast_rules"]
    assert isinstance(rules, list), "fail_fast_rules must be a list"
    assert len(rules) >= 3,         f"Expected >= 3 fail_fast_rules, got {len(rules)}"
    for r in rules:
        assert isinstance(r, str),  f"Each rule must be str, got: {r!r}"

    threshold_rule_present = any("< target" in r or "actual" in r.lower() for r in rules)
    assert threshold_rule_present, (
        "Expected at least one rule referencing 'actual < target' semantics. "
        f"Rules: {rules}"
    )

    exit_rule_present = any("exit 1" in r or "FAIL" in r for r in rules)
    assert exit_rule_present, (
        "Expected at least one rule referencing 'exit 1' or 'FAIL'. "
        f"Rules: {rules}"
    )


# ---------------------------------------------------------------------------
# T1
# ---------------------------------------------------------------------------

def test_acceptance_meta_offline_writer_fields_present():
    """T1: build_acceptance_definitions() produces a fully-valid payload offline."""
    _validate_payload(_SAMPLE_PAYLOAD)


# ---------------------------------------------------------------------------
# T2
# ---------------------------------------------------------------------------

def test_acceptance_meta_offline_writer_roundtrip_valid_json(tmp_path):
    """T2: write → read → validate round-trip through JSON serialisation."""
    dest = tmp_path / "acceptance_definitions.meta.json"
    write_acceptance_definitions_meta(dest, _SAMPLE_PAYLOAD)

    assert dest.exists(), "File was not created by write_acceptance_definitions_meta"
    raw = dest.read_text(encoding="utf-8")

    # Must parse as valid JSON
    reloaded = json.loads(raw)

    # Numeric targets must survive round-trip as int (JSON int, not float)
    assert isinstance(reloaded["z0_pool_targets"]["min_total_items"],    int)
    assert isinstance(reloaded["z0_pool_targets"]["min_frontier85_72h"], int)
    for field in ("events", "product", "tech", "business"):
        assert isinstance(reloaded["kpi_targets"][field], int)

    _validate_payload(reloaded)


# ---------------------------------------------------------------------------
# T3
# ---------------------------------------------------------------------------

def test_acceptance_meta_real_outputs_if_present_else_offline_only():
    """T3: if real meta exists → validate it; otherwise validate offline payload.

    This test NEVER skips.  When the real file is absent (offline CI / fresh
    checkout), it verifies the offline writer contract instead and documents
    that with an explicit assertion.
    """
    if _REAL_META_PATH.exists():
        # Online-run branch: validate the actual file written by verify_online.ps1
        real_payload = json.loads(_REAL_META_PATH.read_text(encoding="utf-8"))
        _validate_payload(real_payload)
    else:
        # Offline-only branch: validate the offline writer contract
        # (no skip — offline CI always exercises this path)
        offline_payload = build_acceptance_definitions(
            run_head="offline-ci-head",
            collected_at=None,
            z0_min_total_items=800,
            z0_min_frontier85_72h=10,
            kpi_min_events=6,
            kpi_min_product=2,
            kpi_min_tech=2,
            kpi_min_business=2,
            fail_fast_rules=list(DEFAULT_FAIL_FAST_RULES),
        )
        _validate_payload(offline_payload)
        assert True, "offline-only branch: writer contract validated without real meta file"
