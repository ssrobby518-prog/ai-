# install_daily_9am_task.ps1
# Install Windows Task Scheduler task: daily 09:00 Beijing time (China Standard Time).
# Task name: AIIntelScraper_Daily_0900_Beijing
# Writes outputs/scheduler.meta.json on success.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\install_daily_9am_task.ps1
# Requires: Administrator privileges

$ErrorActionPreference = "Stop"

$taskName   = "AIIntelScraper_Daily_0900_Beijing"
$repoRoot   = (Split-Path $PSScriptRoot -Parent)
$scriptPath = Join-Path $repoRoot "scripts\run_pipeline.ps1"
$outDir     = Join-Path $repoRoot "outputs"
$metaPath   = Join-Path $outDir "scheduler.meta.json"

Write-Host "=== Installing Daily Scheduled Task (Beijing 09:00) ===" -ForegroundColor Cyan
Write-Host "  Task Name : $taskName"
Write-Host "  Script    : $scriptPath"
Write-Host "  Schedule  : Daily at 09:00 China Standard Time"
Write-Host ""

# Validate script target
if (-not (Test-Path $scriptPath)) {
    Write-Host "ERROR: Script not found: $scriptPath" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Convert Beijing 09:00 -> local machine time using .NET TimeZoneInfo
# ---------------------------------------------------------------------------
try {
    $cstZone    = [System.TimeZoneInfo]::FindSystemTimeZoneById("China Standard Time")
    $localZone  = [System.TimeZoneInfo]::Local
    # Build a reference DateTime in CST at 09:00 today
    $todayLocal = [System.DateTime]::Now.Date
    $todayCst   = [System.TimeZoneInfo]::ConvertTime($todayLocal, $localZone, $cstZone)
    $cst0900    = [System.DateTime]::new($todayCst.Year, $todayCst.Month, $todayCst.Day, 9, 0, 0)
    $local0900  = [System.TimeZoneInfo]::ConvertTime($cst0900, $cstZone, $localZone)
    $triggerTime = $local0900.ToString("HH:mm")
    Write-Host ("  Beijing 09:00 = local {0} (TZ: {1})" -f $triggerTime, $localZone.Id) -ForegroundColor DarkGray
} catch {
    Write-Host ("  WARN: TZ conversion failed ({0}); falling back to 09:00 local" -f $_) -ForegroundColor Yellow
    $triggerTime = "09:00"
}

# ---------------------------------------------------------------------------
# Remove existing task (idempotent)
# ---------------------------------------------------------------------------
$existing = schtasks /Query /TN $taskName 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host ("  Removing existing task '{0}'..." -f $taskName) -ForegroundColor Yellow
    schtasks /Delete /TN $taskName /F | Out-Null
    Write-Host "  Removed." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Create task
# ---------------------------------------------------------------------------
$scriptAbs = (Resolve-Path $scriptPath).Path
$action    = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptAbs`""

Write-Host ("  Action: {0}" -f $action) -ForegroundColor DarkGray

schtasks /Create `
    /TN $taskName `
    /TR "`"$action`"" `
    /SC DAILY `
    /ST $triggerTime `
    /RL HIGHEST `
    /F

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: schtasks /Create failed." -ForegroundColor Red
    Write-Host "  Try running this script as Administrator." -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------------------
# Compute next-run timestamp (Beijing)
# ---------------------------------------------------------------------------
$nextRunBeijing = ""
try {
    $cstZone2   = [System.TimeZoneInfo]::FindSystemTimeZoneById("China Standard Time")
    $localZone2 = [System.TimeZoneInfo]::Local
    $now        = [System.DateTime]::Now
    $nowCst     = [System.TimeZoneInfo]::ConvertTime($now, $localZone2, $cstZone2)
    # Next 09:00 Beijing
    $next09     = [System.DateTime]::new($nowCst.Year, $nowCst.Month, $nowCst.Day, 9, 0, 0)
    if ($nowCst -ge $next09) { $next09 = $next09.AddDays(1) }
    $nextRunBeijing = $next09.ToString("yyyy-MM-ddTHH:mm:ss") + "+08:00"
} catch {
    $nextRunBeijing = "(unknown)"
}

# ---------------------------------------------------------------------------
# Write scheduler.meta.json
# ---------------------------------------------------------------------------
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }
$meta = [ordered]@{
    task_name          = $taskName
    installed          = $true
    trigger_time_local = $triggerTime
    trigger_tz_source  = "China Standard Time -> local"
    next_run_at_beijing = $nextRunBeijing
    last_run           = $null
    script_path        = $scriptAbs
    installed_at       = (Get-Date -Format "o")
}
$meta | ConvertTo-Json -Depth 3 | Out-File -FilePath $metaPath -Encoding UTF8 -NoNewline
Write-Host ""
Write-Host ("scheduler.meta.json written: {0}" -f $metaPath) -ForegroundColor Green

Write-Host ""
Write-Host "=== Task installed successfully ===" -ForegroundColor Green
Write-Host ("  '{0}' fires daily at {1} local ({2} Beijing)" -f $taskName, $triggerTime, "09:00")
Write-Host ("  Next run (Beijing): {0}" -f $nextRunBeijing)
Write-Host ""
Write-Host "  To verify : schtasks /Query /TN $taskName"
Write-Host "  To run now: schtasks /Run   /TN $taskName"
Write-Host "  To remove : powershell -File scripts\uninstall_daily_task.ps1"
