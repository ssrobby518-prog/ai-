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
