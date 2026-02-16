# generate_reports.ps1 ??One-click executive report generation
# Usage:
#   Desktop shortcut (via open_ppt.ps1):
#     powershell -ExecutionPolicy Bypass -File scripts\generate_reports.ps1 -OpenPpt
#   Manual / VSCode task:
#     powershell -ExecutionPolicy Bypass -File scripts\generate_reports.ps1
#   Scheduled (headless):
#     powershell -ExecutionPolicy Bypass -File scripts\generate_reports.ps1 -NoOpenPpt

param(
    [switch] $NoOpenPpt,
    [switch] $OpenPpt,
    [switch] $SmokeTest
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
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-Host "`n--- Diagnostics ($ts) ---" -ForegroundColor DarkGray
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
    $isInteractive = [Environment]::UserInteractive -and ($Host.Name -match 'ConsoleHost|Visual Studio Code Host|Windows Terminal')
    $shouldOpen = $isInteractive
    $openReason = "No switch ??UserInteractive=$([Environment]::UserInteractive), Host=$($Host.Name), isInteractive=$isInteractive"
}
Write-Host "  shouldOpen       : $shouldOpen  ($openReason)" -ForegroundColor $(if ($shouldOpen) { "Green" } else { "DarkGray" })
Write-Host "-------------------`n" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# Resolve project root
# ---------------------------------------------------------------------------
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot

# Prefer repo-local Python environments to avoid desktop shortcut interpreter drift.
$venvPython = Join-Path $projectRoot "venv\Scripts\python.exe"
$dotVenvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $py = $venvPython
} elseif (Test-Path $dotVenvPython) {
    $py = $dotVenvPython
} else {
    Write-Host "ERROR: No repo-local Python found (expected venv\\Scripts\\python.exe or .venv\\Scripts\\python.exe)." -ForegroundColor Red
    Write-Host "       Desktop mode requires a stable local interpreter to generate PPT." -ForegroundColor Red
    exit 1
}

Write-Host "Using Python interpreter: $py" -ForegroundColor DarkGray

# Validate required report deps before execution.
& $py -c "import pptx, docx" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Required packages missing in selected Python (python-pptx / python-docx)." -ForegroundColor Red
    Write-Host "       Interpreter: $py" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Run pipeline
# ---------------------------------------------------------------------------
if ($SmokeTest) {
    Write-Host "Running desktop-entry smoke mode (offline, no network fetch)..." -ForegroundColor Yellow
    @'
from datetime import datetime
from pathlib import Path

from core.ppt_generator import generate_executive_ppt
from schemas.education_models import EduNewsCard, SystemHealthReport

card = EduNewsCard(
    item_id="desktop-smoke-001",
    is_valid_news=True,
    title_plain="Desktop launcher validation signal",
    what_happened="Launcher validation mode generated an executive presentation artifact.",
    why_important="Validates desktop entry path can produce PPTX.",
    source_name="smoke",
    source_url="https://example.com/smoke",
    final_score=8.0,
)
health = SystemHealthReport(success_rate=100.0, p50_latency=0.1, p95_latency=0.2)
out_dir = Path("outputs")
out_dir.mkdir(parents=True, exist_ok=True)
canonical = out_dir / "executive_report.pptx"
tmp = out_dir / f"executive_report_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.pptx"
generate_executive_ppt(
    cards=[card],
    health=health,
    report_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
    total_items=1,
    output_path=tmp,
)
try:
    tmp.replace(canonical)
except OSError:
    # If canonical file is locked by an opened viewer, keep the new smoke artifact.
    pass
print(f"SMOKE_PPTX={tmp.resolve()}")
'@ | & $py -
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        Write-Host "Smoke mode failed (exit code: $exitCode)" -ForegroundColor Red
        exit 1
    }
    Write-Host "Smoke mode completed successfully." -ForegroundColor Green
} else {
    Write-Host "Running analysis pipeline..." -ForegroundColor Yellow
    & $py scripts/run_once.py
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        Write-Host "Pipeline failed (exit code: $exitCode)" -ForegroundColor Red
        exit 1
    }
    Write-Host "Pipeline completed successfully." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Verify all 4 output files exist and show sizes
# ---------------------------------------------------------------------------
Write-Host "`n=== Output Files ===" -ForegroundColor Cyan

if ($SmokeTest) {
    $files = @(
        "outputs\executive_report.pptx"
    )
} else {
    $files = @(
        "outputs\executive_report.docx",
        "outputs\executive_report.pptx",
        "outputs\notion_page.md",
        "outputs\mindmap.xmind"
    )
}

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

function Get-LatestExecutivePptPath {
    param([string]$Root)

    $outputsDir = Join-Path $Root "outputs"
    $candidates = Get-ChildItem -Path $outputsDir -Filter "executive_report*.pptx" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notmatch "smoke" } |
        Sort-Object LastWriteTime -Descending

    if ($candidates -and $candidates.Count -gt 0) {
        return $candidates[0].FullName
    }
    return (Join-Path $Root "outputs\executive_report.pptx")
}

$pptxPath = Get-LatestExecutivePptPath -Root $projectRoot
Write-Host "PPT generated successfully: $pptxPath" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Open PPT (only when shouldOpen = True)
# ---------------------------------------------------------------------------
if (-not $shouldOpen) {
    Write-Host "`n  Headless mode - skipping PPT open." -ForegroundColor DarkGray
    exit 0
}
$minOpenBytes = 30720
if (-not (Test-Path $pptxPath)) {
    Write-Host "ERROR: PPT file not found for auto-open: $pptxPath" -ForegroundColor Red
    exit 2
}
$pptxAbs = (Resolve-Path $pptxPath).Path

$preOpenSize = (Get-Item $pptxAbs).Length
if ($preOpenSize -lt $minOpenBytes) {
    Write-Host "ERROR: PPT file too small for open contract ($preOpenSize bytes < $minOpenBytes bytes)." -ForegroundColor Red
    exit 2
}

$opened = $false
$lastError = ""

Write-Host "`n--- Open PPT ---" -ForegroundColor Cyan
Write-Host "PPT_PATH=$pptxAbs"

for ($attempt = 1; $attempt -le 5; $attempt++) {
    Write-Host "OpenAttempt $attempt/5"

    if (-not (Test-Path $pptxAbs)) {
        Write-Host "ERROR: PPT file disappeared before open attempt: $pptxAbs" -ForegroundColor Red
        exit 2
    }

    $pptxSize = (Get-Item $pptxAbs).Length
    if ($pptxSize -lt $minOpenBytes) {
        Write-Host "ERROR: PPT file below open threshold ($pptxSize bytes < $minOpenBytes bytes)." -ForegroundColor Red
        exit 2
    }
    Write-Host "  File size    : $pptxSize bytes" -ForegroundColor DarkGray

    try {
        $proc = Start-Process -FilePath $pptxAbs -PassThru -ErrorAction Stop
        Start-Sleep -Milliseconds 900
        $pptProc = Get-Process -Name "POWERPNT" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $proc -or $null -ne $pptProc) {
            Write-Host "  OpenAttempt $attempt succeeded." -ForegroundColor Green
            $opened = $true
            break
        }
        throw "No launcher process handle after Start-Process."
    } catch {
        $lastError = $_.Exception.Message
        Write-Host "  OpenAttempt $attempt failed: $lastError" -ForegroundColor Yellow
    }

    $delayMs = Get-Random -Minimum 700 -Maximum 1201
    Start-Sleep -Milliseconds $delayMs
}

if (-not $opened) {
    Write-Host "ERROR: Failed to auto-open PPT after 5 attempts." -ForegroundColor Red
    Write-Host "LAST_OPEN_ERROR: $lastError" -ForegroundColor Red
    Write-Host "PPT remains generated at: $pptxAbs" -ForegroundColor Yellow
    exit 3
}

Write-Host "`n=== Done ===" -ForegroundColor Cyan
