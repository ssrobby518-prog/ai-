# generate_reports.ps1 — One-click executive report generation
# Usage:
#   Manual / VSCode task : powershell -ExecutionPolicy Bypass -File scripts\generate_reports.ps1
#                          → opens PPT automatically (interactive default)
#   Scheduled (headless) : powershell -ExecutionPolicy Bypass -File scripts\generate_reports.ps1 -NoOpenPpt
#                          → never opens any file

param(
    [switch] $NoOpenPpt
)

$ErrorActionPreference = "Stop"

# UTF-8 console hardening
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

Write-Host "=== Executive Report Generator ===" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# Diagnostics — proves how the script was invoked
# ---------------------------------------------------------------------------
Write-Host "`n--- Diagnostics ---" -ForegroundColor DarkGray
Write-Host "  PSCommandPath    : $PSCommandPath" -ForegroundColor DarkGray
Write-Host "  PSScriptRoot     : $PSScriptRoot" -ForegroundColor DarkGray
Write-Host "  PWD              : $($PWD.Path)" -ForegroundColor DarkGray
Write-Host "  NoOpenPpt switch : IsPresent=$($NoOpenPpt.IsPresent)  Value=$NoOpenPpt" -ForegroundColor DarkGray

# Determine if we should open PPT: YES unless -NoOpenPpt was passed
$shouldOpen = -not $NoOpenPpt
Write-Host "  Will open PPT    : $shouldOpen" -ForegroundColor DarkGray

# Check Start-Process availability
$spCmd = Get-Command Start-Process -ErrorAction SilentlyContinue
Write-Host "  Start-Process    : $(if ($spCmd) { 'available' } else { 'NOT FOUND' })" -ForegroundColor DarkGray
Write-Host "-------------------`n" -ForegroundColor DarkGray

# Resolve project root
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot

# Prefer venv python if available
$venvPython = Join-Path $projectRoot "venv\Scripts\python.exe"
if (Test-Path $venvPython) { $py = $venvPython } else { $py = "python" }

# Run pipeline
Write-Host "Running analysis pipeline..." -ForegroundColor Yellow
& $py scripts/run_once.py
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Host "Pipeline failed (exit code: $exitCode)" -ForegroundColor Red
    exit 1
}
Write-Host "Pipeline completed successfully." -ForegroundColor Green

# Verify all 4 output files exist and show sizes
Write-Host "`n=== Output Files ===" -ForegroundColor Cyan

$files = @(
    "outputs\executive_report.docx",
    "outputs\executive_report.pptx",
    "outputs\notion_page.md",
    "outputs\mindmap.xmind"
)

$allExist = $true
foreach ($f in $files) {
    $fullPath = Join-Path $projectRoot $f
    if (Test-Path $fullPath) {
        $info = Get-Item $fullPath
        Write-Host ("  {0,-40} {1,10:N0} bytes" -f $info.FullName, $info.Length) -ForegroundColor Green
    } else {
        Write-Host "  MISSING: $fullPath" -ForegroundColor Red
        $allExist = $false
    }
}

if (-not $allExist) {
    Write-Host "`nSome output files are missing!" -ForegroundColor Red
    exit 1
}

Write-Host "`n=== All reports generated successfully ===" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# Open PPT — only when NOT suppressed (default: open; -NoOpenPpt: skip)
# ---------------------------------------------------------------------------
if ($shouldOpen) {
    $pptxPath = Join-Path $projectRoot "outputs\executive_report.pptx"
    if (Test-Path $pptxPath) {
        try {
            $pptxAbs = (Resolve-Path $pptxPath).Path
            Write-Host "`n  Resolve-Path OK : $pptxAbs" -ForegroundColor DarkGray
            Write-Host "  Opening PPT..." -ForegroundColor Yellow
            Start-Process -FilePath $pptxAbs -ErrorAction Stop
            Write-Host "  Start-Process succeeded." -ForegroundColor Green
        } catch {
            Write-Error "Start-Process failed: $($_.Exception.Message)"
            exit 1
        }
    } else {
        Write-Host "`nWARN: PPT not found at expected path: $pptxPath" -ForegroundColor Red
    }
} else {
    Write-Host "`n  Headless mode — skipping PPT open." -ForegroundColor DarkGray
}
