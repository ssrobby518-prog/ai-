# generate_reports.ps1 — One-click executive report generation
# Usage:
#   Desktop shortcut (via open_ppt.ps1):
#     powershell -ExecutionPolicy Bypass -File scripts\generate_reports.ps1 -OpenPpt
#   Manual / VSCode task:
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
    $openReason = "No switch — UserInteractive=$([Environment]::UserInteractive), Host=$($Host.Name), isInteractive=$isInteractive"
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

# --- Snapshot office processes BEFORE open ---
$procsBefore = @(Get-Process | Where-Object { $_.ProcessName -match "wps|wpp|et|powerpnt|POWERPNT|soffice" } | ForEach-Object { $_.Id })
Write-Host "`n  Office PIDs before open: [$($procsBefore -join ', ')]" -ForegroundColor DarkGray

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

# --- Open chain: cmd start (primary) → Start-Process (fallback) ---
$opened = $false

# Primary: cmd /c start (most reliable in desktop GUI session)
Write-Host "`n  [1/2] cmd /c start..." -ForegroundColor Yellow
try {
    Start-Process cmd.exe -ArgumentList "/c", "start", "`"`"", "`"$pptxAbs`"" -ErrorAction Stop
    Write-Host "  [1/2] cmd /c start succeeded." -ForegroundColor Green
    $opened = $true
} catch {
    Write-Host "  [1/2] cmd /c start FAILED" -ForegroundColor Red
    Write-Host "    Exception : $($_.Exception.GetType().FullName)" -ForegroundColor Red
    Write-Host "    Message   : $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "    HResult   : $($_.Exception.HResult)" -ForegroundColor Red
}

# Fallback: Start-Process directly on the file (shell execute)
if (-not $opened) {
    Write-Host "`n  [2/2] Start-Process on file..." -ForegroundColor Yellow
    try {
        Start-Process -FilePath $pptxAbs -ErrorAction Stop
        Write-Host "  [2/2] Start-Process succeeded." -ForegroundColor Green
        $opened = $true
    } catch {
        Write-Host "  [2/2] Start-Process FAILED" -ForegroundColor Red
        Write-Host "    Exception : $($_.Exception.GetType().FullName)" -ForegroundColor Red
        Write-Host "    Message   : $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "    HResult   : $($_.Exception.HResult)" -ForegroundColor Red
    }
}

if (-not $opened) {
    Write-Error "Both open methods failed. File is at: $pptxAbs"
    exit 1
}

# ---------------------------------------------------------------------------
# Post-verification: process diff + Win32 window foreground
# ---------------------------------------------------------------------------

# Load Win32 APIs for window enumeration and foreground
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;

public class Win32Window {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();

    public const int SW_RESTORE = 9;

    public static List<KeyValuePair<IntPtr, string>> GetVisibleWindows() {
        var result = new List<KeyValuePair<IntPtr, string>>();
        EnumWindows((hWnd, lParam) => {
            if (IsWindowVisible(hWnd)) {
                int len = GetWindowTextLength(hWnd);
                if (len > 0) {
                    var sb = new StringBuilder(len + 1);
                    GetWindowText(hWnd, sb, sb.Capacity);
                    result.Add(new KeyValuePair<IntPtr, string>(hWnd, sb.ToString()));
                }
            }
            return true;
        }, IntPtr.Zero);
        return result;
    }

    public static bool BringToFront(IntPtr hWnd) {
        ShowWindow(hWnd, SW_RESTORE);
        return SetForegroundWindow(hWnd);
    }
}
"@

Write-Host "`n--- Post-verification (two-phase window search) ---" -ForegroundColor Cyan

$targetWindow = $null
$maxWait = 10
$elapsed = 0

# Phase 1: search by filename pattern in window title
$phase1Patterns = @("executive_report_open", "\.pptx")
Write-Host "  Phase 1: Searching window title for filename pattern (max ${maxWait}s)..." -ForegroundColor DarkGray

while ($elapsed -lt $maxWait) {
    Start-Sleep -Seconds 1
    $elapsed++

    $windows = [Win32Window]::GetVisibleWindows()
    foreach ($w in $windows) {
        foreach ($pat in $phase1Patterns) {
            if ($w.Value -match $pat) {
                $targetWindow = $w
                break
            }
        }
        if ($targetWindow) { break }
    }
    if ($targetWindow) { break }

    # Log new PIDs as they appear
    $procsAfter = @(Get-Process | Where-Object { $_.ProcessName -match "wps|wpp|et|powerpnt|POWERPNT|soffice" } | ForEach-Object { $_.Id })
    $newPids = $procsAfter | Where-Object { $_ -notin $procsBefore }
    if ($newPids -and $elapsed -ge 2) {
        Write-Host "  New office PIDs detected: [$($newPids -join ', ')] (window not yet titled)" -ForegroundColor DarkGray
    }
}

# Phase 2 fallback: if Phase 1 missed, search by new Office PID windows
if (-not $targetWindow) {
    Write-Host "  Phase 1 miss. Phase 2: Searching by new Office PID windows..." -ForegroundColor Yellow
    $procsAfter = @(Get-Process | Where-Object { $_.ProcessName -match "wps|wpp|et|powerpnt|POWERPNT|soffice" } | ForEach-Object { $_.Id })
    $newPids = $procsAfter | Where-Object { $_ -notin $procsBefore }

    if ($newPids) {
        Write-Host "  New Office PIDs: [$($newPids -join ', ')]" -ForegroundColor DarkGray
        # Enumerate all visible windows and match office-like titles
        $officePattern = "office|ppt|powerpoint|presentation|wps|report|簡報"
        $windows = [Win32Window]::GetVisibleWindows()
        Write-Host "  Visible windows matching office keywords:" -ForegroundColor DarkGray
        foreach ($w in $windows) {
            if ($w.Value -match $officePattern) {
                Write-Host "    hWnd=$($w.Key) Title='$($w.Value)'" -ForegroundColor DarkGray
                if (-not $targetWindow) { $targetWindow = $w }
            }
        }
    } else {
        Write-Host "  ERROR: No office process detected after ${maxWait}s." -ForegroundColor Red
        Write-Host "  Root cause: (d) No Office application installed or file association broken." -ForegroundColor Red
        Write-Host "  File: $pptxAbs" -ForegroundColor Red
        Write-Error "PPT did NOT open — no Office process found. File: $pptxAbs"
        exit 1
    }
}

if ($targetWindow) {
    Write-Host "  Window FOUND at ${elapsed}s: '$($targetWindow.Value)'" -ForegroundColor Green
    Write-Host "  Handle: $($targetWindow.Key)" -ForegroundColor DarkGray

    # Force to foreground
    $fgResult = [Win32Window]::BringToFront($targetWindow.Key)
    Write-Host "  SetForegroundWindow: $fgResult" -ForegroundColor $(if ($fgResult) { "Green" } else { "Yellow" })

    # Verify it's now foreground
    $fgNow = [Win32Window]::GetForegroundWindow()
    if ($fgNow -eq $targetWindow.Key) {
        Write-Host "  CONFIRMED: PPT is foreground window." -ForegroundColor Green
    } else {
        Write-Host "  NOTE: PPT opened but may not be topmost (another window has focus)." -ForegroundColor Yellow
    }
} else {
    # PIDs exist but no window found — root cause (c)
    Write-Host "  ERROR: Office process started but no presentation window visible after ${maxWait}s." -ForegroundColor Red
    Write-Host "  Root cause: (c) Office launched but window not visible/in background." -ForegroundColor Red
    Write-Host "  Dumping all visible windows for diagnosis:" -ForegroundColor Yellow
    $allWin = [Win32Window]::GetVisibleWindows()
    foreach ($w in $allWin) {
        Write-Host "    hWnd=$($w.Key) Title='$($w.Value)'" -ForegroundColor DarkGray
    }
    Write-Error "PPT window not visible after open. Manual mode requires visible window."
    exit 1
}

Write-Host "`n=== Done ===" -ForegroundColor Cyan
