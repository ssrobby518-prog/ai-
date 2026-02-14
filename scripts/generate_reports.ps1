# generate_reports.ps1 — One-click executive report generation
# Usage:
#   Manual / VSCode / Desktop shortcut:
#     powershell -ExecutionPolicy Bypass -File scripts\generate_reports.ps1
#     powershell -ExecutionPolicy Bypass -File scripts\generate_reports.ps1 -OpenPpt
#     → opens executive_report_open.pptx automatically (default behaviour)
#   Scheduled (headless):
#     powershell -ExecutionPolicy Bypass -File scripts\generate_reports.ps1 -NoOpenPpt
#     → never opens any file

param(
    [switch] $NoOpenPpt,
    [switch] $OpenPpt
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
Write-Host "  Host.Name        : $($Host.Name)" -ForegroundColor DarkGray
Write-Host "  SESSIONNAME      : $($env:SESSIONNAME)" -ForegroundColor DarkGray
Write-Host "  TERM_PROGRAM     : $($env:TERM_PROGRAM)" -ForegroundColor DarkGray
Write-Host "  WT_SESSION       : $($env:WT_SESSION)" -ForegroundColor DarkGray
Write-Host "  NoOpenPpt        : IsPresent=$($NoOpenPpt.IsPresent)  Value=$NoOpenPpt" -ForegroundColor DarkGray
Write-Host "  OpenPpt          : IsPresent=$($OpenPpt.IsPresent)  Value=$OpenPpt" -ForegroundColor DarkGray

# Determine if we should open PPT:
#   -NoOpenPpt → never open (scheduled)
#   -OpenPpt   → always open (desktop shortcut explicit)
#   neither    → default = open (manual / VSCode task)
if ($NoOpenPpt) {
    $shouldOpen = $false
} elseif ($OpenPpt) {
    $shouldOpen = $true
} else {
    $shouldOpen = $true
}
Write-Host "  shouldOpen       : $shouldOpen" -ForegroundColor DarkGray

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
# Open PPT — copy to _open.pptx first, then triple fallback open
# ---------------------------------------------------------------------------
if ($shouldOpen) {
    $pptxPath = Join-Path $projectRoot "outputs\executive_report.pptx"
    if (Test-Path $pptxPath) {
        $pptxAbs = (Resolve-Path $pptxPath).Path
        Write-Host "`n  Resolve-Path OK : $pptxAbs" -ForegroundColor DarkGray

        # Copy to _open.pptx to dodge PowerPoint file lock
        $pptxOpenAbs = Join-Path $projectRoot "outputs\executive_report_open.pptx"
        Copy-Item $pptxAbs $pptxOpenAbs -Force
        Write-Host "  Copy-Item OK    : $pptxOpenAbs" -ForegroundColor DarkGray

        $pptOpenResolved = (Resolve-Path $pptxOpenAbs).Path
        Write-Host "  Opening PPT (triple fallback)..." -ForegroundColor Yellow
        $opened = $false

        # Fallback 1: cmd /c start
        try {
            Write-Host "  [1/3] cmd /c start..." -ForegroundColor DarkGray
            Start-Process cmd.exe -ArgumentList "/c", "start", "`"`"", "`"$pptOpenResolved`"" -ErrorAction Stop
            Write-Host "  [1/3] succeeded." -ForegroundColor Green
            $opened = $true
        } catch {
            Write-Host "  [1/3] failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }

        # Fallback 2: Invoke-Item
        if (-not $opened) {
            try {
                Write-Host "  [2/3] Invoke-Item..." -ForegroundColor DarkGray
                Invoke-Item $pptOpenResolved -ErrorAction Stop
                Write-Host "  [2/3] succeeded." -ForegroundColor Green
                $opened = $true
            } catch {
                Write-Host "  [2/3] failed: $($_.Exception.Message)" -ForegroundColor Yellow
            }
        }

        # Fallback 3: explorer.exe
        if (-not $opened) {
            try {
                Write-Host "  [3/3] explorer.exe..." -ForegroundColor DarkGray
                Start-Process explorer.exe $pptOpenResolved -ErrorAction Stop
                Write-Host "  [3/3] succeeded." -ForegroundColor Green
                $opened = $true
            } catch {
                Write-Host "  [3/3] failed: $($_.Exception.Message)" -ForegroundColor Red
            }
        }

        if (-not $opened) {
            Write-Error "All 3 open methods failed. File: $pptOpenResolved"
            exit 1
        }
    } else {
        Write-Host "`nWARN: PPT not found at expected path: $pptxPath" -ForegroundColor Red
    }
} else {
    Write-Host "`n  Headless mode — skipping PPT open." -ForegroundColor DarkGray
}
