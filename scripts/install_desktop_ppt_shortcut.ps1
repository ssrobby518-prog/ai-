# install_desktop_ppt_shortcut.ps1 — Create/update desktop shortcut for open_ppt.ps1
# Idempotent: overwrites existing shortcut with same name.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_desktop_ppt_shortcut.ps1

$ErrorActionPreference = "Stop"

Write-Host "=== Desktop Shortcut Installer ===" -ForegroundColor Cyan
Write-Host "  Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host ""

# ---------------------------------------------------------------------------
# (1) Resolve repo root and target script
# ---------------------------------------------------------------------------
$repoRoot = Split-Path $PSScriptRoot -Parent
$openPptScript = Join-Path $repoRoot "scripts\open_ppt.ps1"

if (-not (Test-Path $openPptScript)) {
    Write-Host "ERROR: open_ppt.ps1 not found at: $openPptScript" -ForegroundColor Red
    exit 1
}

Write-Host "  Repo root      : $repoRoot" -ForegroundColor Green
Write-Host "  Target script  : $openPptScript" -ForegroundColor Green
Write-Host ""

# ---------------------------------------------------------------------------
# (2) Detect desktop paths
# ---------------------------------------------------------------------------
$shortcutName = "Executive PPT (Open).lnk"
$desktops = @()

$standardDesktop = Join-Path $env:USERPROFILE "Desktop"
if (Test-Path $standardDesktop) {
    $desktops += $standardDesktop
    Write-Host "  Desktop found  : $standardDesktop" -ForegroundColor Green
}

$onedriveDesktop = Join-Path $env:USERPROFILE "OneDrive\Desktop"
if (Test-Path $onedriveDesktop) {
    $desktops += $onedriveDesktop
    Write-Host "  Desktop found  : $onedriveDesktop (OneDrive)" -ForegroundColor Green
}

if ($desktops.Count -eq 0) {
    Write-Host "ERROR: No Desktop directory found!" -ForegroundColor Red
    exit 1
}

if ($desktops.Count -gt 1) {
    Write-Host "  NOTE: Multiple desktops detected — shortcut will be placed on BOTH." -ForegroundColor Yellow
}
Write-Host ""

# ---------------------------------------------------------------------------
# (3) Create/update shortcut on each desktop
# ---------------------------------------------------------------------------
$wsh = New-Object -ComObject WScript.Shell
$created = 0

foreach ($desktop in $desktops) {
    $lnkPath = Join-Path $desktop $shortcutName
    Write-Host "--- Creating shortcut ---" -ForegroundColor Cyan
    Write-Host "  Path: $lnkPath" -ForegroundColor White

    $sc = $wsh.CreateShortcut($lnkPath)
    $sc.TargetPath = "powershell.exe"
    $sc.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$openPptScript`""
    $sc.WorkingDirectory = $repoRoot
    $sc.Description = "Open executive PPT report"
    $sc.Save()

    # Read back and verify
    $verify = $wsh.CreateShortcut($lnkPath)
    Write-Host "  [Verify] TargetPath       : $($verify.TargetPath)" -ForegroundColor Green
    Write-Host "  [Verify] Arguments        : $($verify.Arguments)" -ForegroundColor Green
    Write-Host "  [Verify] WorkingDirectory : $($verify.WorkingDirectory)" -ForegroundColor Green
    Write-Host ""
    $created++
}

Write-Host "=== Done: $created shortcut(s) created/updated ===" -ForegroundColor Cyan
