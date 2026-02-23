# FILE: scripts\run_pipeline.ps1
# Desktop-button entry point: runs the real pipeline (run_once.py),
# writes outputs/desktop_button.meta.json, opens latest output on success.
# Params:
#   -Mode     manual|daily   (default: manual; Task Scheduler passes "daily")
#   -AutoOpen true|false     (default: true;   Task Scheduler passes "false" to suppress UI)
param(
    [string]$Mode     = "manual",
    [string]$AutoOpen = "true"
)
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# Generate run_id = YYYYMMDD_HHMMSS (Beijing time if possible, else local)
$RunId = (Get-Date -Format "yyyyMMdd_HHmmss")
$StartAt = (Get-Date -Format "o")

Write-Host "=== AI Intel Scraper — run_id=$RunId ===" -ForegroundColor Cyan

# Pass run_id into run_once.py via env var so it writes desktop_button.meta.json with correct run_id
$env:PIPELINE_RUN_ID       = $RunId
$env:PIPELINE_TRIGGERED_BY = "run_pipeline.ps1"

# Run the real pipeline
python scripts/run_once.py
$ExitCode = $LASTEXITCODE

$env:PIPELINE_RUN_ID       = $null
$env:PIPELINE_TRIGGERED_BY = $null
$FinishedAt = (Get-Date -Format "o")

# Write desktop_button.meta.json
$MetaDir = Join-Path $RepoRoot "outputs"
if (-not (Test-Path $MetaDir)) { New-Item -ItemType Directory -Path $MetaDir | Out-Null }
$MetaPath = Join-Path $MetaDir "desktop_button.meta.json"
$MetaObj = [ordered]@{
    run_id        = $RunId
    started_at    = $StartAt
    finished_at   = $FinishedAt
    exit_code     = $ExitCode
    success       = ($ExitCode -eq 0)
    pipeline      = "scripts/run_once.py"
    triggered_by  = "run_pipeline.ps1"
}
$MetaObj | ConvertTo-Json -Depth 3 | Out-File -FilePath $MetaPath -Encoding UTF8 -NoNewline
Write-Host ("desktop_button.meta.json written: exit_code={0}" -f $ExitCode)

if ($ExitCode -eq 0 -and $AutoOpen -ne "false") {
    # Open latest output (try open_latest.ps1, fallback to open_ppt.ps1)
    # Suppressed when -AutoOpen false (e.g. scheduled/headless runs)
    $OpenLatest = Join-Path $RepoRoot "scripts\open_latest.ps1"
    $OpenPpt    = Join-Path $RepoRoot "scripts\open_ppt.ps1"
    if (Test-Path $OpenLatest) {
        & $OpenLatest
    } elseif (Test-Path $OpenPpt) {
        & $OpenPpt
    }
}

exit $ExitCode
