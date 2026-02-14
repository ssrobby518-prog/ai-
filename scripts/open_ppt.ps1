# open_ppt.ps1 — Desktop shortcut entry point (thin wrapper)
# Only job: locate repo root, call generate_reports.ps1 -OpenPpt, relay exit code.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\open_ppt.ps1

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# (0) Force repo root — first line, does not depend on shortcut "Start in"
# ---------------------------------------------------------------------------
$repoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $repoRoot

# ---------------------------------------------------------------------------
# (0.5) Shortcut existence hint (interactive only, no side effects)
# ---------------------------------------------------------------------------
if ([Environment]::UserInteractive) {
    $desktopDir = Join-Path $env:USERPROFILE "Desktop"
    $expectedLnk = Join-Path $desktopDir "Executive PPT (Open).lnk"
    if (-not (Test-Path $expectedLnk)) {
        Write-Host "[HINT] Desktop shortcut 'Executive PPT (Open).lnk' not found." -ForegroundColor Yellow
        Write-Host "       Run:  powershell -NoProfile -ExecutionPolicy Bypass -File `"$repoRoot\scripts\install_desktop_ppt_shortcut.ps1`"" -ForegroundColor Yellow
        Write-Host ""
    }
}

# ---------------------------------------------------------------------------
# (1) Diagnostics
# ---------------------------------------------------------------------------
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-Host "=== Desktop PPT Launcher (open_ppt.ps1) [$ts] ===" -ForegroundColor Cyan
Write-Host "  PSCommandPath      : $PSCommandPath" -ForegroundColor DarkGray
Write-Host "  PSScriptRoot       : $PSScriptRoot" -ForegroundColor DarkGray
Write-Host "  PWD                : $($PWD.Path)" -ForegroundColor DarkGray
Write-Host "  Host.Name          : $($Host.Name)" -ForegroundColor DarkGray
Write-Host "  SESSIONNAME        : $($env:SESSIONNAME)" -ForegroundColor DarkGray
Write-Host "  USERNAME           : $($env:USERNAME)" -ForegroundColor DarkGray
Write-Host "  UserInteractive    : $([Environment]::UserInteractive)" -ForegroundColor DarkGray
Write-Host "  Repo root          : $repoRoot" -ForegroundColor DarkGray

# Verify PPT source exists before calling generate
$pptxSrc = Join-Path $repoRoot "outputs\executive_report.pptx"
Write-Host "  PPT source exists  : $(Test-Path $pptxSrc)" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# (2) Call generate_reports.ps1 with -OpenPpt (forces open)
# ---------------------------------------------------------------------------
$generateScript = Join-Path $repoRoot "scripts\generate_reports.ps1"

if (-not (Test-Path $generateScript)) {
    Write-Error "generate_reports.ps1 not found at: $generateScript"
    exit 1
}

$cmdLine = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$generateScript`" -OpenPpt"
Write-Host "`n  Calling: $cmdLine" -ForegroundColor Yellow

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $generateScript -OpenPpt
$genExit = $LASTEXITCODE

Write-Host "`n  generate_reports.ps1 exit code: $genExit" -ForegroundColor $(if ($genExit -eq 0) { "Green" } else { "Red" })

# ---------------------------------------------------------------------------
# (3) Shortcut copy-paste instructions
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Desktop Shortcut Target (copy-paste) ===" -ForegroundColor Cyan
Write-Host "  Target:   powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -ForegroundColor White
Write-Host "  Start in: (leave empty)" -ForegroundColor White
Write-Host ""

exit $genExit
