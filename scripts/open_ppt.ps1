# open_ppt.ps1 — Desktop shortcut entry point (self-contained)
# Flow: generate reports (headless) → copy to _open.pptx → open via triple fallback
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\open_ppt.ps1

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# (0) Diagnostics — prove exactly how this script was invoked
# ---------------------------------------------------------------------------
Write-Host "=== Desktop PPT Launcher ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "--- Diagnostics ---" -ForegroundColor DarkGray
Write-Host "  PSCommandPath : $PSCommandPath" -ForegroundColor DarkGray
Write-Host "  PSScriptRoot  : $PSScriptRoot" -ForegroundColor DarkGray
Write-Host "  PWD           : $($PWD.Path)" -ForegroundColor DarkGray
Write-Host "  Host.Name     : $($Host.Name)" -ForegroundColor DarkGray
Write-Host "  SESSIONNAME   : $($env:SESSIONNAME)" -ForegroundColor DarkGray
Write-Host "  WT_SESSION    : $($env:WT_SESSION)" -ForegroundColor DarkGray
Write-Host "  TERM_PROGRAM  : $($env:TERM_PROGRAM)" -ForegroundColor DarkGray

$pwshCmd = Get-Command powershell.exe -ErrorAction SilentlyContinue
Write-Host "  powershell.exe: $(if ($pwshCmd) { $pwshCmd.Source } else { 'NOT FOUND' })" -ForegroundColor DarkGray
$explorerCmd = Get-Command explorer.exe -ErrorAction SilentlyContinue
Write-Host "  explorer.exe  : $(if ($explorerCmd) { $explorerCmd.Source } else { 'NOT FOUND' })" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# (1) Force repo root — does not depend on shortcut "Start in"
# ---------------------------------------------------------------------------
$repoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $repoRoot
Write-Host "  Repo root     : $repoRoot" -ForegroundColor DarkGray

$pptxSrc  = Join-Path $repoRoot "outputs\executive_report.pptx"
$pptxOpen = Join-Path $repoRoot "outputs\executive_report_open.pptx"

Write-Host "  PPT source    : $(Test-Path $pptxSrc)  ($pptxSrc)" -ForegroundColor DarkGray
Write-Host "  PPT open copy : $(Test-Path $pptxOpen) ($pptxOpen)" -ForegroundColor DarkGray
Write-Host "-------------------" -ForegroundColor DarkGray
Write-Host ""

# ---------------------------------------------------------------------------
# (2) Generate reports — always headless (open_ppt.ps1 owns the open step)
# ---------------------------------------------------------------------------
$generateScript = Join-Path $PSScriptRoot "generate_reports.ps1"

if (-not (Test-Path $generateScript)) {
    Write-Error "generate_reports.ps1 not found at: $generateScript"
    exit 1
}

Write-Host "Generating reports (headless)..." -ForegroundColor Yellow
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $generateScript -NoOpenPpt
$genExit = $LASTEXITCODE

if ($genExit -ne 0) {
    Write-Error "generate_reports.ps1 failed (exit code: $genExit)"
    exit $genExit
}
Write-Host "Reports generated." -ForegroundColor Green
Write-Host ""

# ---------------------------------------------------------------------------
# (3) Copy to _open.pptx (dodge PowerPoint file lock from previous run)
# ---------------------------------------------------------------------------
if (-not (Test-Path $pptxSrc)) {
    Write-Host "ERROR: PPT not found after generation." -ForegroundColor Red
    Write-Host "Contents of outputs/:" -ForegroundColor Yellow
    Get-ChildItem (Join-Path $repoRoot "outputs") -ErrorAction SilentlyContinue | Format-Table Name, Length, LastWriteTime
    exit 1
}

Copy-Item -Path $pptxSrc -Destination $pptxOpen -Force
Write-Host "  Copy-Item OK: $pptxOpen" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# (4) Open via triple fallback: cmd start → Invoke-Item → explorer.exe
# ---------------------------------------------------------------------------
$pptAbs = (Resolve-Path $pptxOpen).Path
Write-Host "  Resolve-Path OK: $pptAbs" -ForegroundColor DarkGray
Write-Host "  Opening PPT (triple fallback)..." -ForegroundColor Yellow

$opened = $false

# Fallback 1: cmd /c start (most reliable for desktop shortcuts)
try {
    Write-Host "  [1/3] Trying: cmd /c start..." -ForegroundColor DarkGray
    Start-Process cmd.exe -ArgumentList "/c", "start", "`"`"", "`"$pptAbs`"" -ErrorAction Stop
    Write-Host "  [1/3] cmd /c start succeeded." -ForegroundColor Green
    $opened = $true
} catch {
    Write-Host "  [1/3] cmd /c start failed: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Fallback 2: Invoke-Item
if (-not $opened) {
    try {
        Write-Host "  [2/3] Trying: Invoke-Item..." -ForegroundColor DarkGray
        Invoke-Item $pptAbs -ErrorAction Stop
        Write-Host "  [2/3] Invoke-Item succeeded." -ForegroundColor Green
        $opened = $true
    } catch {
        Write-Host "  [2/3] Invoke-Item failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

# Fallback 3: explorer.exe
if (-not $opened) {
    try {
        Write-Host "  [3/3] Trying: explorer.exe..." -ForegroundColor DarkGray
        Start-Process -FilePath "explorer.exe" -ArgumentList @("`"$pptAbs`"") -ErrorAction Stop
        Write-Host "  [3/3] explorer.exe succeeded." -ForegroundColor Green
        $opened = $true
    } catch {
        Write-Host "  [3/3] explorer.exe failed: $($_.Exception.Message)" -ForegroundColor Red
    }
}

if (-not $opened) {
    Write-Error "All 3 open methods failed. File is at: $pptAbs"
    exit 1
}

# ---------------------------------------------------------------------------
# (5) Shortcut copy-paste instructions
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Desktop Shortcut Target (copy-paste) ===" -ForegroundColor Cyan
Write-Host "  Target:   powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -ForegroundColor White
Write-Host "  Start in: (leave empty)" -ForegroundColor White
Write-Host ""

exit 0
