"""Tests for verify_online KPI gate logic and archive HEAD match.

Verifies (offline, no script execution):
- KPI gate helper returns FAIL when actuals < targets (non-sparse-day)
- KPI gate helper returns PASS when actuals >= targets
- archive_head_match is true when CURRENT_HEAD == ARCHIVE_HEAD
- archive_head_match is false when they differ
- exec_kpi.meta.json contains archive_head_match-compatible fields
"""
from __future__ import annotations

import json
import pytest


# ---------------------------------------------------------------------------
# KPI gate logic (mirrors verify_online.ps1 gate computation in Python)
# ---------------------------------------------------------------------------

def _kpi_gate(actual: int, target: int, sparse_day: bool) -> str:
    """Replicate the PowerShell gate logic: PASS if actual >= target OR sparse_day."""
    return "PASS" if (actual >= target or sparse_day) else "FAIL"


def test_kpi_gate_pass_when_actual_meets_target():
    assert _kpi_gate(2, 2, False) == "PASS"
    assert _kpi_gate(3, 2, False) == "PASS"
    assert _kpi_gate(6, 6, False) == "PASS"


def test_kpi_gate_fail_when_actual_below_target_non_sparse():
    assert _kpi_gate(1, 2, False) == "FAIL"
    assert _kpi_gate(0, 2, False) == "FAIL"
    assert _kpi_gate(5, 6, False) == "FAIL"


def test_kpi_gate_pass_on_sparse_day_even_if_below_target():
    """Sparse-day fallback: gate is PASS even if actual < target."""
    assert _kpi_gate(0, 2, True) == "PASS"
    assert _kpi_gate(1, 2, True) == "PASS"


def test_all_gates_must_fail_when_business_is_zero_non_sparse():
    targets = {"events": 6, "product": 2, "tech": 2, "business": 2}
    actuals = {"events": 6, "product": 2, "tech": 2, "business": 0}  # business missing
    sparse_day = False

    gates = {k: _kpi_gate(actuals[k], targets[k], sparse_day) for k in targets}
    assert gates["business"] == "FAIL"
    # Other gates pass
    assert gates["events"] == "PASS"
    assert gates["product"] == "PASS"
    assert gates["tech"] == "PASS"

    any_fail = any(v == "FAIL" for v in gates.values())
    assert any_fail, "any_fail must be True when business=0"


# ---------------------------------------------------------------------------
# Archive HEAD match logic
# ---------------------------------------------------------------------------

def _archive_head_match(current_head: str, archive_head: str) -> str:
    return "PASS" if current_head == archive_head else "FAIL"


def test_archive_head_match_pass_when_equal():
    h = "abc123def456abc123def456abc123def456abc1"
    assert _archive_head_match(h, h) == "PASS"


def test_archive_head_match_fail_when_different():
    h1 = "fe8703a"
    h2 = "cf1abc7"
    assert _archive_head_match(h1, h2) == "FAIL"


def test_archive_dir_name_parsing():
    """Simulate extracting ARCHIVE_HEAD from delivery dir name."""
    import re
    dir_leaf = "20260220_123456_abc123def456"
    # PowerShell: $dirLeaf -replace '^\d{8}_\d{6}_', ''
    archive_head = re.sub(r'^\d{8}_\d{6}_', '', dir_leaf)
    assert archive_head == "abc123def456"


# ---------------------------------------------------------------------------
# exec_kpi.meta.json structure validation
# ---------------------------------------------------------------------------

def test_exec_kpi_meta_structure(tmp_path):
    """exec_kpi.meta.json produced by write_exec_kpi_meta has required fields."""
    import os
    os.environ["EXEC_MIN_EVENTS"]   = "6"
    os.environ["EXEC_MIN_PRODUCT"]  = "2"
    os.environ["EXEC_MIN_TECH"]     = "2"
    os.environ["EXEC_MIN_BUSINESS"] = "2"

    try:
        from core.content_strategy import write_exec_kpi_meta

        sel_meta = {
            "events_total": 7,
            "events_by_bucket": {"product": 2, "tech": 2, "business": 2, "dev": 1},
            "business_backfill": {"candidates_total": 3, "selected_total": 0, "selected_ids": []},
            "product_backfill":  {"candidates_total": 0, "selected_total": 0, "selected_ids": []},
            "tech_backfill":     {"candidates_total": 0, "selected_total": 0, "selected_ids": []},
        }

        write_exec_kpi_meta(sel_meta, project_root=tmp_path)

        out_file = tmp_path / "outputs" / "exec_kpi.meta.json"
        assert out_file.exists()

        data = json.loads(out_file.read_text(encoding="utf-8"))

        # Required top-level keys (now includes product_backfill and tech_backfill)
        assert set(data.keys()) >= {"kpi_targets", "kpi_actuals", "business_backfill",
                                     "product_backfill", "tech_backfill"}

        # kpi_targets
        kt = data["kpi_targets"]
        assert set(kt.keys()) >= {"events", "product", "tech", "business"}
        assert kt["business"] == 2

        # kpi_actuals
        ka = data["kpi_actuals"]
        assert set(ka.keys()) >= {"events", "product", "tech", "business"}
        assert ka["business"] == 2

        # all channel backfills
        for bf_key in ("business_backfill", "product_backfill", "tech_backfill"):
            bb = data[bf_key]
            assert set(bb.keys()) >= {"candidates_total", "selected_total", "selected_ids"}
            assert isinstance(bb["selected_ids"], list)
    finally:
        for k in ["EXEC_MIN_EVENTS", "EXEC_MIN_PRODUCT", "EXEC_MIN_TECH", "EXEC_MIN_BUSINESS"]:
            os.environ.pop(k, None)


def test_exec_kpi_meta_non_breaking_on_write_error(tmp_path):
    """write_exec_kpi_meta must never raise even if outputs dir is read-only."""
    import os
    from core.content_strategy import write_exec_kpi_meta

    # Pass a path that is a file (not a dir) to force an error
    bad_root = tmp_path / "not_a_dir.txt"
    bad_root.write_text("block")

    # Should not raise
    write_exec_kpi_meta({}, project_root=bad_root)
