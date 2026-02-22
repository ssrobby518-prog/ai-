# verify_shortcut_open_ppt.ps1
# A) Verify shortcut TargetPath / WorkingDirectory match expected values.
# B) Run open_ppt.ps1 with 10-second WaitForExit guard (no timeout=124 allowed).

$repoRoot = Split-Path $PSScriptRoot -Parent
$psExe    = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
$openPpt  = Join-Path $repoRoot "scripts\open_ppt.ps1"

# ---------------------------------------------------------------------------
# A) Compute expected values dynamically (avoids hardcoding Chinese in literals)
# ---------------------------------------------------------------------------
$expectedTarget = "`"$psExe`" -NoProfile -ExecutionPolicy Bypass -File `"$openPpt`""
$expectedStart  = $repoRoot

# Find .lnk (OneDrive Desktop first, then plain Desktop, then Public Desktop)
$lnkCandidates = @(
    (Join-Path $env:USERPROFILE "OneDrive\Desktop\Executive PPT (Open).lnk"),
    (Join-Path $env:USERPROFILE "Desktop\Executive PPT (Open).lnk"),
    "C:\Users\Public\Desktop\Executive PPT (Open).lnk"
)

$lnkPath = $null
foreach ($c in $lnkCandidates) {
    if (Test-Path $c) { $lnkPath = $c; break }
}

if (-not $lnkPath) {
    Write-Output "SHORTCUT_PATH: NOT_FOUND"
    Write-Output "TARGET_OK: False"
    Write-Output "STARTIN_OK: False"
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$s     = $shell.CreateShortcut($lnkPath)

$actualTarget = ('"{0}" {1}' -f $s.TargetPath, $s.Arguments).Trim()
$actualStart  = $s.WorkingDirectory

Write-Output "SHORTCUT_PATH: $lnkPath"
Write-Output "TARGET_ACTUAL: $actualTarget"
Write-Output "STARTIN_ACTUAL: $actualStart"
Write-Output ("TARGET_OK: " + ($actualTarget -eq $expectedTarget))
Write-Output ("STARTIN_OK: " + ($actualStart -eq $expectedStart))

# ---------------------------------------------------------------------------
# B) Run open_ppt.ps1 with 10-second WaitForExit guard
# ---------------------------------------------------------------------------
$p = Start-Process -FilePath $psExe `
    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $openPpt) `
    -PassThru -WindowStyle Hidden

if (-not $p.WaitForExit(10000)) {
    $p.Kill()
    Write-Output "OPEN_PPT_RUN: FAIL timeout>10s"
    exit 124
} else {
    Write-Output ("OPEN_PPT_RUN: exit=" + $p.ExitCode)
    exit $p.ExitCode
}
