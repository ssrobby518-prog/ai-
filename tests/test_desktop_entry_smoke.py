from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Desktop shortcut entry is Windows-specific")
def test_generate_reports_desktop_smoke_produces_pptx() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    ps = shutil.which("powershell") or shutil.which("powershell.exe")
    if not ps:
        pytest.skip("powershell not available")

    cmd = [
        ps,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "scripts\\generate_reports.ps1",
        "-SmokeTest",
        "-NoOpenPpt",
    ]
    run = subprocess.run(
        cmd,
        cwd=str(repo_root),
        capture_output=True,
        timeout=180,
        check=False,
    )

    stdout = run.stdout.decode("utf-8", errors="ignore") if run.stdout else ""
    stderr = run.stderr.decode("utf-8", errors="ignore") if run.stderr else ""

    assert run.returncode == 0, stdout + "\n" + stderr
    pptx_path = repo_root / "outputs" / "executive_report.pptx"
    assert pptx_path.exists()
    assert pptx_path.stat().st_size > 0
    assert "PPT generated successfully:" in stdout
