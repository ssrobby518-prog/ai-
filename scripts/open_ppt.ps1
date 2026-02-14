# open_ppt.ps1 — Desktop shortcut entry point
# Purpose: Stable entry for Windows desktop shortcut / right-click menu.
#          Always generates reports AND opens PPT. Never headless.
# Usage:   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\open_ppt.ps1
# Shortcut target:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Projects\ai捕捉資訊\ai-intel-scraper-mvp\scripts\open_ppt.ps1"

$ErrorActionPreference = "Stop"

# (1) Force repo root regardless of Start-in, admin elevation, or CWD
$repoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $repoRoot

Write-Host "=== Desktop PPT Launcher ===" -ForegroundColor Cyan
Write-Host "  Repo root : $repoRoot" -ForegroundColor DarkGray
Write-Host "  Working dir: $(Get-Location)" -ForegroundColor DarkGray

# Run generate_reports.ps1 with -OpenPpt (explicit open)
$generateScript = Join-Path $PSScriptRoot "generate_reports.ps1"

if (-not (Test-Path $generateScript)) {
    Write-Error "generate_reports.ps1 not found at: $generateScript"
    exit 1
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $generateScript -OpenPpt
$genExit = $LASTEXITCODE

if ($genExit -ne 0) {
    Write-Error "generate_reports.ps1 failed (exit code: $genExit)"
    exit $genExit
}

# (2) Verify PPT exists with absolute path
$pptPath = Resolve-Path "outputs/executive_report_open.pptx" -ErrorAction SilentlyContinue

if (-not $pptPath) {
    # Fallback to original if _open copy wasn't created
    $pptPath = Resolve-Path "outputs/executive_report.pptx" -ErrorAction SilentlyContinue
}

if (-not $pptPath) {
    Write-Error "PPT not found: outputs/executive_report.pptx"
    exit 1
}

# (3) Verify it wasn't already opened by generate_reports.ps1
#     generate_reports.ps1 -OpenPpt already calls Start-Process,
#     so we only need to confirm success here.
Write-Host "  PPT opened: $pptPath" -ForegroundColor Green

exit 0
