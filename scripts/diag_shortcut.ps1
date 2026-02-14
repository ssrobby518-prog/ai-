# diag_shortcut.ps1 — Read-only diagnostic: inspect desktop shortcuts
# Lists all .lnk files on Desktop that reference open_ppt.ps1 or generate_reports.ps1
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\diag_shortcut.ps1

$ErrorActionPreference = "SilentlyContinue"

Write-Host "=== Desktop Shortcut Diagnostic ===" -ForegroundColor Cyan
Write-Host "  Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host ""

# ---------------------------------------------------------------------------
# (1) Explicit desktop path enumeration
# ---------------------------------------------------------------------------
$standardDesktop = [Environment]::GetFolderPath("Desktop")
$oneDriveDesktop = if ($env:OneDrive) { Join-Path $env:OneDrive "Desktop" } else { $null }

Write-Host "--- Desktop Paths ---" -ForegroundColor Yellow
Write-Host "  [Environment]::GetFolderPath('Desktop')" -ForegroundColor DarkGray
Write-Host "    Path   : $standardDesktop" -ForegroundColor White
Write-Host "    Exists : $(Test-Path $standardDesktop)" -ForegroundColor $(if (Test-Path $standardDesktop) { "Green" } else { "Red" })

if ($oneDriveDesktop) {
    Write-Host "  Join-Path `$env:OneDrive 'Desktop'" -ForegroundColor DarkGray
    Write-Host "    Path   : $oneDriveDesktop" -ForegroundColor White
    Write-Host "    Exists : $(Test-Path $oneDriveDesktop)" -ForegroundColor $(if (Test-Path $oneDriveDesktop) { "Green" } else { "Red" })
} else {
    Write-Host "  `$env:OneDrive is not set — skipping OneDrive Desktop" -ForegroundColor DarkGray
}
Write-Host ""

# Build unique list of existing desktop paths
$desktopPaths = @($standardDesktop)
if ($oneDriveDesktop) { $desktopPaths += $oneDriveDesktop }
$desktopPaths = $desktopPaths | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique

# ---------------------------------------------------------------------------
# (2) Scan .lnk files for open_ppt.ps1 / generate_reports.ps1
# ---------------------------------------------------------------------------
$shell = New-Object -ComObject WScript.Shell
$found = 0
$allLnkNames = @()

foreach ($dp in $desktopPaths) {
    $lnkFiles = Get-ChildItem -Path $dp -Filter "*.lnk" -ErrorAction SilentlyContinue
    foreach ($lnk in $lnkFiles) {
        $allLnkNames += $lnk.Name
        $shortcut = $shell.CreateShortcut($lnk.FullName)
        $combined = "$($shortcut.TargetPath) $($shortcut.Arguments)"
        if ($combined -match "open_ppt|generate_reports") {
            $found++
            Write-Host "--- Matching Shortcut #$found ---" -ForegroundColor Cyan
            Write-Host "  ShortcutName     : $($lnk.Name)" -ForegroundColor White
            Write-Host "  FullPath         : $($lnk.FullName)" -ForegroundColor White
            Write-Host "  TargetPath       : $($shortcut.TargetPath)" -ForegroundColor White
            Write-Host "  Arguments        : $($shortcut.Arguments)" -ForegroundColor White
            Write-Host "  WorkingDirectory : $($shortcut.WorkingDirectory)" -ForegroundColor White
            Write-Host "  IconLocation     : $($shortcut.IconLocation)" -ForegroundColor DarkGray
            Write-Host "  WindowStyle      : $($shortcut.WindowStyle)" -ForegroundColor DarkGray

            # Validate target exists
            if ($shortcut.TargetPath -and (Test-Path $shortcut.TargetPath)) {
                Write-Host "  TargetPath exists: YES" -ForegroundColor Green
            } else {
                Write-Host "  TargetPath exists: NO (broken shortcut!)" -ForegroundColor Red
            }

            # Check if the script file in arguments exists
            if ($shortcut.Arguments -match '-File\s+"?([^"]+)"?') {
                $scriptFile = $Matches[1].Trim('"')
                if (Test-Path $scriptFile) {
                    Write-Host "  Script file OK   : $scriptFile" -ForegroundColor Green
                } else {
                    Write-Host "  Script file MISSING: $scriptFile" -ForegroundColor Red
                }
            }
            Write-Host ""
        }
    }
}

if ($found -eq 0) {
    Write-Host "========================================================" -ForegroundColor Red
    Write-Host "  WARN: No shortcuts found referencing open_ppt.ps1" -ForegroundColor Red
    Write-Host "        or generate_reports.ps1 on any Desktop path!" -ForegroundColor Red
    Write-Host "========================================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "  All .lnk files found on Desktop:" -ForegroundColor Yellow
    if ($allLnkNames.Count -gt 0) {
        foreach ($n in ($allLnkNames | Sort-Object -Unique)) {
            Write-Host "    - $n" -ForegroundColor White
        }
    } else {
        Write-Host "    (none)" -ForegroundColor DarkGray
    }
    Write-Host ""
    Write-Host "  Suggested shortcut target:" -ForegroundColor Yellow
    $suggestedScript = Join-Path (Split-Path $PSScriptRoot -Parent) "scripts\open_ppt.ps1"
    Write-Host "    powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$suggestedScript`"" -ForegroundColor White
}

Write-Host "`n=== Done ===" -ForegroundColor Cyan
