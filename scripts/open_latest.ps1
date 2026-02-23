# open_latest.ps1 — Open latest delivery output (PPT first, then DOCX fallback).
# Delegates to open_ppt.ps1 which handles pointer + delivery scan + fallback logic.
$ErrorActionPreference = 'SilentlyContinue'
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$OpenPpt = Join-Path $RepoRoot "scripts\open_ppt.ps1"
if (Test-Path $OpenPpt) {
    & $OpenPpt
    exit $LASTEXITCODE
}
Write-Output "OPEN_LATEST: open_ppt.ps1 not found"
exit 2
