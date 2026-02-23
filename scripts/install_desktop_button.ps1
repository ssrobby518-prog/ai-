# FILE: scripts\install_desktop_button.ps1
# Stage 4 Desktop Button — create a Windows .lnk shortcut on the Desktop.
# Shortcut name : AIIntelScraper_Run_MVP
# Shortcut action: powershell.exe -File scripts\run_pipeline.ps1 -Mode manual -AutoOpen true
# Desktop path  : [Environment]::GetFolderPath("Desktop") (OneDrive-safe)
# Idempotent    : overwrites existing shortcut if present
# Usage         : powershell -ExecutionPolicy Bypass -File scripts\install_desktop_button.ps1

$ErrorActionPreference = "Stop"

$repoRoot      = Split-Path -Parent $PSScriptRoot
$scriptPath    = Join-Path $repoRoot "scripts\run_pipeline.ps1"
$shortcutName  = "AIIntelScraper_Run_MVP.lnk"
$desktopPath   = [Environment]::GetFolderPath("Desktop")
$lnkPath       = Join-Path $desktopPath $shortcutName

Write-Host "=== Install Desktop Button ===" -ForegroundColor Cyan
Write-Host ("  Repo root   : {0}" -f $repoRoot)
Write-Host ("  Script      : {0}" -f $scriptPath)
Write-Host ("  Desktop     : {0}" -f $desktopPath)
Write-Host ("  Shortcut    : {0}" -f $lnkPath)
Write-Host ""

if (-not (Test-Path $scriptPath)) {
    Write-Host ("ERROR: run_pipeline.ps1 not found: {0}" -f $scriptPath) -ForegroundColor Red
    exit 1
}

# Resolve powershell.exe full path (avoids ambiguity with pwsh / PowerShell 7)
$psExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path $psExe)) {
    # Fallback: resolve from PATH
    $psExe = (Get-Command powershell.exe -ErrorAction Stop).Source
}

# Build shortcut arguments: -File with quoted absolute path, -Mode manual, -AutoOpen true
$lnkArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Normal -File `"$scriptPath`" -Mode manual -AutoOpen true"

# Create/overwrite the shortcut via WScript.Shell COM
$wsh = New-Object -ComObject WScript.Shell
$lnk = $wsh.CreateShortcut($lnkPath)
$lnk.TargetPath       = $psExe
$lnk.Arguments        = $lnkArgs
$lnk.WorkingDirectory = $repoRoot
$lnk.Description      = "AI Intel Scraper — run full pipeline and open latest report"
$lnk.Save()

Write-Host "Shortcut created:" -ForegroundColor Green
Write-Host ("  Path       : {0}" -f $lnkPath)
Write-Host ("  TargetPath : {0}" -f $psExe)
Write-Host ("  Arguments  : {0}" -f $lnkArgs)
Write-Host ("  WorkingDir : {0}" -f $repoRoot)
Write-Host ""
Write-Host "=== Install complete ===" -ForegroundColor Green
Write-Host "  Double-click the shortcut on your Desktop to run the full pipeline."
Write-Host "  To uninstall: powershell -ExecutionPolicy Bypass -File scripts\uninstall_desktop_button.ps1"
Write-Host ""
