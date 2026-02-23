# FILE: scripts\uninstall_desktop_button.ps1
# Remove the AIIntelScraper_Run_MVP desktop shortcut.
# Idempotent: exits cleanly if shortcut is not present.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\uninstall_desktop_button.ps1

$ErrorActionPreference = "Stop"

$shortcutName = "AIIntelScraper_Run_MVP.lnk"
$desktopPath  = [Environment]::GetFolderPath("Desktop")
$lnkPath      = Join-Path $desktopPath $shortcutName

Write-Host "=== Uninstall Desktop Button ===" -ForegroundColor Cyan
Write-Host ("  Shortcut: {0}" -f $lnkPath)

if (Test-Path $lnkPath) {
    Remove-Item $lnkPath -Force
    Write-Host "  Removed." -ForegroundColor Green
} else {
    Write-Host "  Not found — nothing to remove." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Uninstall complete ===" -ForegroundColor Green
