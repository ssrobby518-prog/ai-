"""Minimal stdlib helper — Z0 pool health gate evaluation.

Used by tests to verify gate logic independently of the PowerShell
implementation in scripts/verify_online.ps1.  No external dependencies.

Gate defaults mirror verify_online.ps1:
    min_total   = 800   (Z0_MIN_TOTAL_ITEMS env override)
    min_85_72h  =  10   (Z0_MIN_FRONTIER85_72H env override)
"""
from __future__ import annotations

_DEFAULT_MIN_TOTAL   = 800
_DEFAULT_MIN_85_72H  = 10


def evaluate_z0_pool_gates(
    total_items:   int,
    frontier85_72h: int,
    min_total:   int = _DEFAULT_MIN_TOTAL,
    min_85_72h:  int = _DEFAULT_MIN_85_72H,
) -> dict:
    """Evaluate Z0 pool health gates.

    Returns::

        {
            "pass":               bool,
            "total_items_gate":   "PASS" | "FAIL",
            "frontier85_72h_gate": "PASS" | "FAIL",
            "reasons":            list[str],   # empty when pass=True
        }
    """
    reasons: list[str] = []

    gate_total = "PASS" if total_items >= min_total else "FAIL"
    if gate_total == "FAIL":
        reasons.append(
            f"total_items={total_items} < min_total={min_total}"
        )

    gate_85_72h = "PASS" if frontier85_72h >= min_85_72h else "FAIL"
    if gate_85_72h == "FAIL":
        reasons.append(
            f"frontier85_72h={frontier85_72h} < min_85_72h={min_85_72h}"
        )

    return {
        "pass":                len(reasons) == 0,
        "total_items_gate":    gate_total,
        "frontier85_72h_gate": gate_85_72h,
        "reasons":             reasons,
    }
