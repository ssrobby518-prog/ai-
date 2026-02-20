"""Contract tests for Z0 pool health gate logic (offline — no PowerShell executed).

Verifies that utils/z0_pool_gates.evaluate_z0_pool_gates() implements the same
gate semantics as the PowerShell block in scripts/verify_online.ps1:

  Z0_MIN_TOTAL_ITEMS    default 800
  Z0_MIN_FRONTIER85_72H default  10

Gate rules:
  - PASS when actual >= target
  - FAIL when actual < target  (→ exit 1 in PS)
  - Both gates independent; one failure → overall fail
"""
from __future__ import annotations

import pytest

from utils.z0_pool_gates import evaluate_z0_pool_gates


# ---------------------------------------------------------------------------
# Default-threshold tests (mirror PowerShell defaults: 800 / 10)
# ---------------------------------------------------------------------------

def test_gate_pass_when_both_meet_default_exactly():
    result = evaluate_z0_pool_gates(total_items=800, frontier85_72h=10)
    assert result["pass"] is True
    assert result["total_items_gate"]    == "PASS"
    assert result["frontier85_72h_gate"] == "PASS"
    assert result["reasons"] == []


def test_gate_pass_when_both_exceed_defaults():
    """Typical healthy run: 1700+ items, 50+ frontier_ge_85_72h."""
    result = evaluate_z0_pool_gates(total_items=1700, frontier85_72h=54)
    assert result["pass"] is True
    assert result["total_items_gate"]    == "PASS"
    assert result["frontier85_72h_gate"] == "PASS"


def test_gate_fail_when_total_items_below_min():
    result = evaluate_z0_pool_gates(total_items=799, frontier85_72h=10)
    assert result["pass"] is False
    assert result["total_items_gate"]    == "FAIL"
    assert result["frontier85_72h_gate"] == "PASS"
    assert len(result["reasons"]) == 1
    assert "total_items=799" in result["reasons"][0]


def test_gate_fail_when_frontier85_72h_below_min():
    result = evaluate_z0_pool_gates(total_items=900, frontier85_72h=9)
    assert result["pass"] is False
    assert result["total_items_gate"]    == "PASS"
    assert result["frontier85_72h_gate"] == "FAIL"
    assert len(result["reasons"]) == 1
    assert "frontier85_72h=9" in result["reasons"][0]


def test_gate_fail_when_both_below_min():
    result = evaluate_z0_pool_gates(total_items=100, frontier85_72h=0)
    assert result["pass"] is False
    assert result["total_items_gate"]    == "FAIL"
    assert result["frontier85_72h_gate"] == "FAIL"
    assert len(result["reasons"]) == 2


def test_gate_fail_at_zero_items():
    """Edge case: empty collection."""
    result = evaluate_z0_pool_gates(total_items=0, frontier85_72h=0)
    assert result["pass"] is False
    assert result["total_items_gate"]    == "FAIL"
    assert result["frontier85_72h_gate"] == "FAIL"


def test_gate_pass_just_at_boundary():
    """Boundary: exactly at threshold → PASS (>= semantics)."""
    result = evaluate_z0_pool_gates(total_items=800, frontier85_72h=10)
    assert result["pass"] is True


def test_gate_fail_one_below_boundary():
    """Boundary: one below threshold → FAIL (>= semantics)."""
    result = evaluate_z0_pool_gates(total_items=800, frontier85_72h=9)
    assert result["pass"] is False
    assert result["frontier85_72h_gate"] == "FAIL"


def test_gate_fail_total_one_below_boundary():
    result = evaluate_z0_pool_gates(total_items=799, frontier85_72h=10)
    assert result["pass"] is False
    assert result["total_items_gate"] == "FAIL"


# ---------------------------------------------------------------------------
# Custom-threshold tests (env-override path)
# ---------------------------------------------------------------------------

def test_custom_thresholds_pass():
    result = evaluate_z0_pool_gates(total_items=500, frontier85_72h=5, min_total=400, min_85_72h=3)
    assert result["pass"] is True


def test_custom_thresholds_fail_total():
    result = evaluate_z0_pool_gates(total_items=399, frontier85_72h=5, min_total=400, min_85_72h=3)
    assert result["pass"] is False
    assert result["total_items_gate"] == "FAIL"


def test_custom_thresholds_zero_means_always_pass():
    """Setting min=0 should always pass (allows disabling a gate via env=0)."""
    result = evaluate_z0_pool_gates(total_items=0, frontier85_72h=0, min_total=0, min_85_72h=0)
    assert result["pass"] is True


# ---------------------------------------------------------------------------
# Reasons list content tests
# ---------------------------------------------------------------------------

def test_reasons_contain_actual_and_min_values():
    result = evaluate_z0_pool_gates(total_items=300, frontier85_72h=2, min_total=800, min_85_72h=10)
    assert any("300" in r for r in result["reasons"])
    assert any("800" in r for r in result["reasons"])
    assert any("2"   in r for r in result["reasons"])
    assert any("10"  in r for r in result["reasons"])
