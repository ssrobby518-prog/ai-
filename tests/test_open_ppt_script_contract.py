from __future__ import annotations

import re
from pathlib import Path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_open_ppt_script_prints_ppt_path_and_calls_generate_openppt() -> None:
    text = _read(Path("scripts/open_ppt.ps1"))

    assert "PPT_PATH=" in text
    assert re.search(r"generate_reports\.ps1\s+-OpenPpt", text, flags=re.IGNORECASE)
    assert "Set-Location $repoRoot" in text


def test_generate_reports_open_contract_has_retry_and_openattempt() -> None:
    text = _read(Path("scripts/generate_reports.ps1"))

    assert "Get-LatestExecutivePptPath" in text
    assert 'Name -notmatch "smoke"' in text
    assert "OpenAttempt" in text
    assert "Start-Process -FilePath $pptxAbs" in text
    assert "for ($attempt = 1; $attempt -le 5; $attempt++)" in text
    assert "$minOpenBytes = 30720" in text
    assert "exit 2" in text
    assert "exit 3" in text
    assert "PPT_PATH=$pptxAbs" in text

    # Observable diagnostics markers (v5.4)
    assert "[OPEN] pptx_path=" in text
    assert "[OPEN] exists=" in text
    assert "PPT generated successfully:" in text


def test_generate_reports_has_fallback_explorer() -> None:
    text = _read(Path("scripts/generate_reports.ps1"))
    assert "explorer.exe" in text
    assert "[OPEN] fallback=" in text or "[OPEN] start_process_exit_code=" in text


def test_verify_run_evidence_has_git_status_line_count() -> None:
    text = _read(Path("scripts/verify_run.ps1"))
    assert "git status -sb (lines=" in text


def test_verify_run_evidence_gate_checks_lines_count() -> None:
    """Evidence gate must fail when status -sb returns != 1 line."""
    text = _read(Path("scripts/verify_run.ps1"))
    assert "gitStatusSbLines.Count -ne 1" in text, (
        "Evidence gate missing: verify_run.ps1 must exit 1 when git status -sb != 1 line"
    )


def test_verify_run_no_base64_note() -> None:
    """Base64-encoded note must be removed (was dirty→clean messaging)."""
    text = _read(Path("scripts/verify_run.ps1"))
    assert "noteBase64" not in text, (
        "noteBase64 still present — remove the Base64-encoded note block"
    )
