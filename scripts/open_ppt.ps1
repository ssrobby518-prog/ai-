# open_ppt.ps1 — Desktop shortcut entry point
# Purpose: Stable entry for Windows desktop shortcut / right-click menu.
#          Always generates reports AND opens PPT. Never headless.
# Usage:   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\open_ppt.ps1
# Shortcut target:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Projects\ai捕捉資訊\ai-intel-scraper-mvp\scripts\open_ppt.ps1"

$ErrorActionPreference = "Stop"

# Force repo root regardless of Start-in, admin elevation, or CWD
$repoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $repoRoot

$generateScript = Join-Path $PSScriptRoot "generate_reports.ps1"

if (-not (Test-Path $generateScript)) {
    Write-Host "ERROR: generate_reports.ps1 not found at: $generateScript" -ForegroundColor Red
    exit 1
}

# Delegate to generate_reports.ps1 with -OpenPpt (explicit open)
# generate_reports.ps1 handles: pipeline + copy to _open.pptx + Start-Process
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $generateScript -OpenPpt

exit $LASTEXITCODE
