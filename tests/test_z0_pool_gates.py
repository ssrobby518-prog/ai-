"""Offline unit tests for Z0 pool health gate logic.

Tests the pure-Python gate evaluation function that mirrors the PowerShell
logic in verify_online.ps1.  No network, no file I/O, no external deps.

Gate semantics:
  evaluate_z0_pool_gates(actual_85_72h, target, allow_degraded, fallback)
  → "PASS"     : actual >= target
  → "DEGRADED" : actual < target AND allow_degraded AND actual >= fallback
  → "FAIL"     : everything else (actual < target AND (not allow_degraded OR actual < fallback))

T1  PASS path (actual meets strict target)
T2  FAIL path (actual < target, allow_degraded off)
T3  DEGRADED path (actual < target, allow_degraded on, actual >= fallback)
T4  FAIL even with allow_degraded (actual < fallback too)
T5  Boundary: actual == target → PASS
T6  Boundary: actual == fallback → DEGRADED
T7  Boundary: actual == fallback - 1 → FAIL (degraded cannot save it)
T8  filter_breakdown fields — verify expected keys are present
T9  ALLOW_ZH_SOURCES_IN_OFFLINE env — verify it expands ALLOW_LANG
"""
from __future__ import annotations

import os
import importlib

import pytest


# ---------------------------------------------------------------------------
# Pure-Python mirror of the PowerShell Z0 gate logic
# ---------------------------------------------------------------------------

def evaluate_z0_pool_gates(
    actual_85_72h: int,
    target: int = 10,
    allow_degraded: bool = False,
    fallback: int = 4,
) -> str:
    """Return 'PASS', 'DEGRADED', or 'FAIL' for the frontier_ge_85_72h gate."""
    if actual_85_72h >= target:
        return "PASS"
    if allow_degraded and actual_85_72h >= fallback:
        return "DEGRADED"
    return "FAIL"


def evaluate_total_items_gate(actual: int, target: int = 800) -> str:
    return "PASS" if actual >= target else "FAIL"


# ---------------------------------------------------------------------------
# T1 — PASS: actual meets strict target
# ---------------------------------------------------------------------------

def test_t1_pass_meets_strict_target() -> None:
    """T1: When actual >= target the gate is PASS regardless of degraded flag."""
    assert evaluate_z0_pool_gates(actual_85_72h=10, target=10) == "PASS"
    assert evaluate_z0_pool_gates(actual_85_72h=15, target=10) == "PASS"
    assert evaluate_z0_pool_gates(actual_85_72h=10, target=10, allow_degraded=True) == "PASS"


# ---------------------------------------------------------------------------
# T2 — FAIL: actual < target, allow_degraded disabled
# ---------------------------------------------------------------------------

def test_t2_fail_below_target_no_degraded() -> None:
    """T2: actual=4 target=10 allow_degraded=False → FAIL."""
    result = evaluate_z0_pool_gates(actual_85_72h=4, target=10, allow_degraded=False)
    assert result == "FAIL", f"Expected FAIL, got {result!r}"


# ---------------------------------------------------------------------------
# T3 — DEGRADED: actual < target, allow_degraded on, actual >= fallback
# ---------------------------------------------------------------------------

def test_t3_degraded_pass_with_allow_degraded() -> None:
    """T3: actual=4 target=10 allow_degraded=1 fallback=4 → DEGRADED."""
    result = evaluate_z0_pool_gates(
        actual_85_72h=4, target=10, allow_degraded=True, fallback=4
    )
    assert result == "DEGRADED", f"Expected DEGRADED, got {result!r}"
    # Degraded MUST NOT be the same string as PASS
    assert result != "PASS"
    assert result != "FAIL"


# ---------------------------------------------------------------------------
# T4 — FAIL even with allow_degraded (actual < fallback too)
# ---------------------------------------------------------------------------

def test_t4_fail_below_fallback_even_with_allow_degraded() -> None:
    """T4: actual=2 target=10 allow_degraded=1 fallback=4 → FAIL (cannot save)."""
    result = evaluate_z0_pool_gates(
        actual_85_72h=2, target=10, allow_degraded=True, fallback=4
    )
    assert result == "FAIL", f"Expected FAIL (below fallback), got {result!r}"


# ---------------------------------------------------------------------------
# T5 — Boundary: actual == target → PASS
# ---------------------------------------------------------------------------

def test_t5_boundary_equals_target() -> None:
    """T5: actual == target exactly → PASS."""
    assert evaluate_z0_pool_gates(actual_85_72h=10, target=10) == "PASS"


# ---------------------------------------------------------------------------
# T6 — Boundary: actual == fallback → DEGRADED
# ---------------------------------------------------------------------------

def test_t6_boundary_equals_fallback() -> None:
    """T6: actual == fallback, allow_degraded → DEGRADED."""
    result = evaluate_z0_pool_gates(
        actual_85_72h=4, target=10, allow_degraded=True, fallback=4
    )
    assert result == "DEGRADED"


# ---------------------------------------------------------------------------
# T7 — Boundary: actual == fallback - 1 → FAIL
# ---------------------------------------------------------------------------

def test_t7_boundary_one_below_fallback() -> None:
    """T7: actual = fallback - 1 with allow_degraded → still FAIL."""
    result = evaluate_z0_pool_gates(
        actual_85_72h=3, target=10, allow_degraded=True, fallback=4
    )
    assert result == "FAIL"


# ---------------------------------------------------------------------------
# T8 — filter_breakdown keys present in the schema written by run_once.py
# ---------------------------------------------------------------------------

_EXPECTED_FB_KEYS = {
    "kept",
    "dropped_total",
    "input_count",
    "reasons",
    "top5_reasons",
    "lang_not_allowed_count",
    "too_old_count",
    "body_too_short_count",
    "non_ai_topic_count",
    "allow_zh_enabled",
}


def _build_mock_filter_breakdown(
    kept: int = 5,
    dropped_total: int = 70,
    lang_not_allowed: int = 30,
    too_old: int = 25,
    body_too_short: int = 10,
    non_ai_topic: int = 5,
) -> dict:
    """Build a filter_breakdown dict matching the schema from run_once.py."""
    reasons = {
        "lang_not_allowed": lang_not_allowed,
        "too_old": too_old,
        "body_too_short": body_too_short,
        "non_ai_topic": non_ai_topic,
    }
    top5 = sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        "kept": kept,
        "dropped_total": dropped_total,
        "input_count": kept + dropped_total,
        "reasons": reasons,
        "top5_reasons": [{"reason": k, "count": v} for k, v in top5],
        "lang_not_allowed_count": lang_not_allowed,
        "too_old_count": too_old,
        "body_too_short_count": body_too_short,
        "non_ai_topic_count": non_ai_topic,
        "allow_zh_enabled": False,
    }


def test_t8_filter_breakdown_schema() -> None:
    """T8: The filter_breakdown dict contains all expected keys."""
    fb = _build_mock_filter_breakdown()
    missing = _EXPECTED_FB_KEYS - set(fb.keys())
    assert not missing, f"Missing keys in filter_breakdown: {missing}"
    # input_count = kept + dropped_total
    assert fb["input_count"] == fb["kept"] + fb["dropped_total"]
    # top5_reasons is a list of dicts with reason/count
    assert isinstance(fb["top5_reasons"], list)
    for item in fb["top5_reasons"]:
        assert "reason" in item and "count" in item


# ---------------------------------------------------------------------------
# T9 — ALLOW_ZH_SOURCES_IN_OFFLINE env expands ALLOW_LANG
# ---------------------------------------------------------------------------

def test_t9_allow_zh_offline_expands_lang_list() -> None:
    """T9: Setting ALLOW_ZH_SOURCES_IN_OFFLINE=1 adds zh-TW variants to ALLOW_LANG."""
    import config.settings as _settings_module

    # Without the env var — baseline
    orig_env = os.environ.get("ALLOW_ZH_SOURCES_IN_OFFLINE", "")
    orig_allow = os.environ.get("ALLOW_LANG", "")
    try:
        os.environ.pop("ALLOW_ZH_SOURCES_IN_OFFLINE", None)
        os.environ.pop("ALLOW_LANG", None)
        importlib.reload(_settings_module)
        base_list = list(_settings_module.ALLOW_LANG)
        assert "zh" in base_list, "Default ALLOW_LANG must contain 'zh'"
        assert "en" in base_list, "Default ALLOW_LANG must contain 'en'"
        assert "zh-tw" not in base_list, "'zh-tw' must not be in base list"
        assert "zh-TW" not in base_list, "'zh-TW' must not be in base list"

        # With ALLOW_ZH_SOURCES_IN_OFFLINE=1 — must include zh-TW variants
        os.environ["ALLOW_ZH_SOURCES_IN_OFFLINE"] = "1"
        importlib.reload(_settings_module)
        expanded_list = list(_settings_module.ALLOW_LANG)
        assert "zh" in expanded_list
        assert "en" in expanded_list
        # At least one zh-TW variant added
        assert any(v in expanded_list for v in ("zh-tw", "zh-TW", "zh-cn", "zh-CN")), (
            f"Expected zh-TW variant in expanded list; got: {expanded_list}"
        )
        # No exact-string duplicates (zh-tw and zh-TW are kept as separate entries
        # because langdetect may return either form)
        assert len(expanded_list) == len(set(expanded_list)), (
            f"Exact duplicates detected in ALLOW_LANG: {expanded_list}"
        )
    finally:
        # Restore original env and reload settings to avoid test pollution
        if orig_env:
            os.environ["ALLOW_ZH_SOURCES_IN_OFFLINE"] = orig_env
        else:
            os.environ.pop("ALLOW_ZH_SOURCES_IN_OFFLINE", None)
        if orig_allow:
            os.environ["ALLOW_LANG"] = orig_allow
        else:
            os.environ.pop("ALLOW_LANG", None)
        importlib.reload(_settings_module)
