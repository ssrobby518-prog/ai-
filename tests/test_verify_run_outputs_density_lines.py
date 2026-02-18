"""Contract tests: verify_run.ps1 must contain density audit markers.

Guards against future removal of the [DENSITY] audit section.
"""

from __future__ import annotations

from pathlib import Path


def _read() -> str:
    return Path("scripts/verify_run.ps1").read_text(encoding="utf-8")


def test_density_audit_section_present() -> None:
    text = _read()
    assert "Executive Slide Density Audit" in text


def test_density_line_marker_present() -> None:
    text = _read()
    assert "[DENSITY]" in text


def test_density_fail_marker_present() -> None:
    text = _read()
    assert "[DENSITY FAIL]" in text


def test_density_required_threshold_configured() -> None:
    text = _read()
    assert "requiredDensity" in text or "EXEC_REQUIRED_SLIDE_DENSITY" in text


def test_key_slide_patterns_present() -> None:
    text = _read()
    # Must cover Overview, Event Ranking, and Pending
    assert "Overview" in text
    assert "Event Ranking" in text or "排行" in text
    assert "Pending" in text or "待決" in text


def test_no_ansi_escape_codes() -> None:
    text = _read()
    assert "\x1b[" not in text, "ANSI escape sequence found"
    assert "`e[" not in text, "PowerShell ANSI escape found"


def test_forbidden_fragments_list_present() -> None:
    text = _read()
    assert "Last July was" in text


def test_slide_density_audit_imported_from_diagnostics() -> None:
    text = _read()
    assert "slide_density_audit" in text
    assert "diagnostics_pptx" in text
