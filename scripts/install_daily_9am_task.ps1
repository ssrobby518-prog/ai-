# install_daily_9am_task.ps1 — Install Windows Task Scheduler task for daily 9am report generation
# Usage: powershell -ExecutionPolicy Bypass -File scripts\install_daily_9am_task.ps1
# Requires: Administrator privileges (for schtasks)
# Idempotent: deletes existing task before creating

$ErrorActionPreference = "Stop"

$taskName = "DailyTechIntelligenceBriefing"
$projectRoot = (Split-Path $PSScriptRoot -Parent)
$scriptPath = Join-Path $projectRoot "scripts\generate_reports.ps1"

Write-Host "=== Installing Daily 9:00 AM Scheduled Task ===" -ForegroundColor Cyan
Write-Host "  Task Name: $taskName"
Write-Host "  Script:    $scriptPath"
Write-Host "  Schedule:  Daily at 09:00"
Write-Host ""

# Check script exists
if (-not (Test-Path $scriptPath)) {
    Write-Host "ERROR: Script not found: $scriptPath" -ForegroundColor Red
    exit 1
}

# Delete existing task if it exists (idempotent)
$existing = schtasks /Query /TN $taskName 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Removing existing task '$taskName'..." -ForegroundColor Yellow
    schtasks /Delete /TN $taskName /F | Out-Null
    Write-Host "  Removed." -ForegroundColor Green
}

# Create new scheduled task
$action = "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""

schtasks /Create `
    /TN $taskName `
    /TR $action `
    /SC DAILY `
    /ST 09:00 `
    /RL HIGHEST `
    /F

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to create scheduled task." -ForegroundColor Red
    Write-Host "  Try running this script as Administrator." -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "=== Task installed successfully ===" -ForegroundColor Green
Write-Host "  The task '$taskName' will run daily at 09:00."
Write-Host "  To verify:  schtasks /Query /TN $taskName"
Write-Host "  To remove:  schtasks /Delete /TN $taskName /F"
Write-Host "  To run now: schtasks /Run /TN $taskName"
