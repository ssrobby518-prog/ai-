# verify_run.ps1 — Education report end-to-end verification
# Purpose: run pipeline with calibration profile, verify FILTER_SUMMARY + Z5 education output
# Usage: powershell -ExecutionPolicy Bypass -File scripts\verify_run.ps1

$ErrorActionPreference = "Stop"
Write-Host "=== Verification Start ===" -ForegroundColor Cyan

# 1) Remove previous education outputs
Write-Host "`n[1/5] Removing previous education outputs..." -ForegroundColor Yellow
$filesToRemove = @(
    "docs\reports\deep_analysis_education_version.md",
    "docs\reports\deep_analysis_education_version_ppt.md",
    "docs\reports\deep_analysis_education_version_xmind.md",
    "outputs\deep_analysis_education.md"
)
foreach ($f in $filesToRemove) {
    if (Test-Path $f) {
        Remove-Item $f -Force
        Write-Host "  Removed: $f"
    }
}

# 2) Run pipeline with calibration profile
Write-Host "`n[2/5] Running pipeline with RUN_PROFILE=calibration..." -ForegroundColor Yellow
$env:RUN_PROFILE = "calibration"
# Prefer venv python if available, otherwise fall back to system python
$venvPython = Join-Path $PSScriptRoot "..\venv\Scripts\python.exe"
if (Test-Path $venvPython) { $py = $venvPython } else { $py = "python" }
& $py scripts/run_once.py
$exitCode = $LASTEXITCODE
$env:RUN_PROFILE = $null  # 清除環境變數

if ($exitCode -ne 0) {
    Write-Host "  Pipeline failed (exit code: $exitCode)" -ForegroundColor Red
    exit 1
}
Write-Host "  Pipeline succeeded" -ForegroundColor Green

# 3) Verify FILTER_SUMMARY exists in log
Write-Host "`n[3/5] Verifying FILTER_SUMMARY log..." -ForegroundColor Yellow
$filterLog = Select-String -Path "logs\app.log" -Pattern "FILTER_SUMMARY" -SimpleMatch | Select-Object -Last 1
if ($filterLog) {
    Write-Host "  FILTER_SUMMARY hit:" -ForegroundColor Green
    Write-Host "  $($filterLog.Line)"
} else {
    Write-Host "  FILTER_SUMMARY not found" -ForegroundColor Red
    exit 1
}

# 4) Verify education report exists
Write-Host "`n[4/5] Checking education report file..." -ForegroundColor Yellow
$eduFile = "docs\reports\deep_analysis_education_version.md"
if (Test-Path $eduFile) {
    Get-Item $eduFile | Format-List FullName, LastWriteTime, Length
} else {
    Write-Host "  Report not found: $eduFile" -ForegroundColor Red
    exit 1
}

# 5) Verify education report contains key sections
Write-Host "[5/5] Verifying education report content..." -ForegroundColor Yellow
$patterns = @("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Metrics", "mermaid")
$hits = Select-String -Path $eduFile -Pattern $patterns -SimpleMatch
if ($hits.Count -ge 3) {
    Write-Host "  Content check passed ($($hits.Count) section hits)" -ForegroundColor Green
} else {
    Write-Host "  Content check failed (only $($hits.Count) hits)" -ForegroundColor Red
}

# Optional: check empty-report markers
$emptyHit = Select-String -Path $eduFile -Pattern "No items|empty|filters" -SimpleMatch
if ($emptyHit) {
    Write-Host "  Empty/non-empty section check:" -ForegroundColor Green
    foreach ($h in $emptyHit) {
        Write-Host "    $($h.Line.Trim().Substring(0, [Math]::Min(80, $h.Line.Trim().Length)))"
    }
}

Write-Host "`n=== Verification Complete ===" -ForegroundColor Cyan
