# generate_reports.ps1 — One-click executive report generation
# Usage:
#   Desktop shortcut (via open_ppt.ps1):
#     powershell -ExecutionPolicy Bypass -File scripts\generate_reports.ps1 -OpenPpt
#   Manual / VSCode task (default = UserInteractive):
#     powershell -ExecutionPolicy Bypass -File scripts\generate_reports.ps1
#   Scheduled (headless):
#     powershell -ExecutionPolicy Bypass -File scripts\generate_reports.ps1 -NoOpenPpt

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
# Diagnostics
# ---------------------------------------------------------------------------
Write-Host "`n--- Diagnostics ---" -ForegroundColor DarkGray
Write-Host "  PSCommandPath    : $PSCommandPath" -ForegroundColor DarkGray
Write-Host "  PSScriptRoot     : $PSScriptRoot" -ForegroundColor DarkGray
Write-Host "  PWD              : $($PWD.Path)" -ForegroundColor DarkGray
Write-Host "  Host.Name        : $($Host.Name)" -ForegroundColor DarkGray
Write-Host "  SESSIONNAME      : $($env:SESSIONNAME)" -ForegroundColor DarkGray
Write-Host "  USERNAME         : $($env:USERNAME)" -ForegroundColor DarkGray
Write-Host "  UserInteractive  : $([Environment]::UserInteractive)" -ForegroundColor DarkGray
Write-Host "  NoOpenPpt        : IsPresent=$($NoOpenPpt.IsPresent)" -ForegroundColor DarkGray
Write-Host "  OpenPpt          : IsPresent=$($OpenPpt.IsPresent)" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# shouldOpen decision (deterministic, logged)
# ---------------------------------------------------------------------------
if ($NoOpenPpt) {
    $shouldOpen = $false
    $openReason = "NoOpenPpt switch forcing headless"
} elseif ($OpenPpt) {
    $shouldOpen = $true
    $openReason = "OpenPpt switch forcing open"
} else {
    $shouldOpen = [Environment]::UserInteractive
    $openReason = "No switch — using UserInteractive=$([Environment]::UserInteractive)"
}
Write-Host "  shouldOpen       : $shouldOpen  ($openReason)" -ForegroundColor $(if ($shouldOpen) { "Green" } else { "DarkGray" })
Write-Host "-------------------`n" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# Resolve project root
# ---------------------------------------------------------------------------
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot

# Prefer venv python if available
$venvPython = Join-Path $projectRoot "venv\Scripts\python.exe"
if (Test-Path $venvPython) { $py = $venvPython } else { $py = "python" }

# ---------------------------------------------------------------------------
# Run pipeline
# ---------------------------------------------------------------------------
Write-Host "Running analysis pipeline..." -ForegroundColor Yellow
& $py scripts/run_once.py
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Host "Pipeline failed (exit code: $exitCode)" -ForegroundColor Red
    exit 1
}
Write-Host "Pipeline completed successfully." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Verify all 4 output files exist and show sizes
# ---------------------------------------------------------------------------
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
# Open PPT (only when shouldOpen = True)
# ---------------------------------------------------------------------------
if (-not $shouldOpen) {
    Write-Host "`n  Headless mode — skipping PPT open." -ForegroundColor DarkGray
    exit 0
}

# --- Copy to _open.pptx (dodge file lock from previous run) ---
$pptxSrc = Join-Path $projectRoot "outputs\executive_report.pptx"
$pptxOpenPath = Join-Path $projectRoot "outputs\executive_report_open.pptx"
Copy-Item $pptxSrc $pptxOpenPath -Force
$pptxAbs = (Resolve-Path $pptxOpenPath).Path
$pptxSize = (Get-Item $pptxAbs).Length

Write-Host "`n--- Open PPT ---" -ForegroundColor Cyan
Write-Host "  Target file  : $pptxAbs" -ForegroundColor Green
Write-Host "  Resolve-Path : OK" -ForegroundColor Green
Write-Host "  File size    : $pptxSize bytes" -ForegroundColor Green
Write-Host "  shouldOpen   : $shouldOpen  ($openReason)" -ForegroundColor Green

# --- 4-layer fallback open chain ---
$opened = $false

# Layer 1: COM ShellExecute (closest to user double-click)
Write-Host "`n  [1/4] COM Shell.Application ShellExecute..." -ForegroundColor Yellow
try {
    $shell = New-Object -ComObject Shell.Application
    $shell.ShellExecute($pptxAbs, $null, $null, "open", 1)
    Write-Host "  [1/4] COM ShellExecute succeeded." -ForegroundColor Green
    $opened = $true
} catch {
    Write-Host "  [1/4] COM ShellExecute FAILED" -ForegroundColor Red
    Write-Host "    Exception : $($_.Exception.GetType().FullName)" -ForegroundColor Red
    Write-Host "    Message   : $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "    HResult   : $($_.Exception.HResult)" -ForegroundColor Red
}

# Layer 2: cmd /c start (Windows shell)
if (-not $opened) {
    Write-Host "`n  [2/4] cmd /c start..." -ForegroundColor Yellow
    try {
        Start-Process cmd.exe -ArgumentList "/c", "start", "`"`"", "`"$pptxAbs`"" -ErrorAction Stop
        Write-Host "  [2/4] cmd /c start succeeded." -ForegroundColor Green
        $opened = $true
    } catch {
        Write-Host "  [2/4] cmd /c start FAILED" -ForegroundColor Red
        Write-Host "    Exception : $($_.Exception.GetType().FullName)" -ForegroundColor Red
        Write-Host "    Message   : $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "    HResult   : $($_.Exception.HResult)" -ForegroundColor Red
    }
}

# Layer 3: Invoke-Item (PowerShell shell open)
if (-not $opened) {
    Write-Host "`n  [3/4] Invoke-Item..." -ForegroundColor Yellow
    try {
        Invoke-Item -LiteralPath $pptxAbs -ErrorAction Stop
        Write-Host "  [3/4] Invoke-Item succeeded." -ForegroundColor Green
        $opened = $true
    } catch {
        Write-Host "  [3/4] Invoke-Item FAILED" -ForegroundColor Red
        Write-Host "    Exception : $($_.Exception.GetType().FullName)" -ForegroundColor Red
        Write-Host "    Message   : $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "    HResult   : $($_.Exception.HResult)" -ForegroundColor Red
    }
}

# Layer 4: explorer.exe (last resort)
if (-not $opened) {
    Write-Host "`n  [4/4] explorer.exe..." -ForegroundColor Yellow
    try {
        Start-Process -FilePath "explorer.exe" -ArgumentList "`"$pptxAbs`"" -WindowStyle Normal -ErrorAction Stop
        Write-Host "  [4/4] explorer.exe succeeded." -ForegroundColor Green
        $opened = $true
    } catch {
        Write-Host "  [4/4] explorer.exe FAILED" -ForegroundColor Red
        Write-Host "    Exception : $($_.Exception.GetType().FullName)" -ForegroundColor Red
        Write-Host "    Message   : $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "    HResult   : $($_.Exception.HResult)" -ForegroundColor Red
    }
}

if (-not $opened) {
    Write-Error "All 4 open methods failed. File is at: $pptxAbs"
    exit 1
}

# --- Post-verification: check if an office process appeared ---
Write-Host "`n  Waiting 2s for office process..." -ForegroundColor DarkGray
Start-Sleep -Seconds 2

$officeProcs = Get-Process | Where-Object { $_.ProcessName -match "wps|et|wpp|powerpnt|POWERPNT|soffice" }
if ($officeProcs) {
    Write-Host "  Post-verify OK: found office process(es):" -ForegroundColor Green
    foreach ($p in $officeProcs) {
        Write-Host "    PID=$($p.Id)  Name=$($p.ProcessName)  Title=$($p.MainWindowTitle)" -ForegroundColor Green
    }
} else {
    Write-Host "  WARNING: No office process detected (wps|powerpnt|soffice)." -ForegroundColor Red
    Write-Host "  The PPT file may not have opened visually." -ForegroundColor Red
    Write-Host "  File location: $pptxAbs" -ForegroundColor Yellow
    # Do NOT exit 1 here — the shell command itself succeeded, and some
    # environments (e.g. CI, remote desktop) may not have Office installed.
    # The warning is enough for the user to diagnose.
}

Write-Host "`n=== Done ===" -ForegroundColor Cyan
