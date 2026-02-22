# setup_desktop_shortcut_open_ppt.ps1
# Create / overwrite "Executive PPT (Open).lnk" on Desktop(s).
# OneDrive Desktop is preferred over plain Desktop when both exist.
# Public Desktop failure is WARN-only (non-fatal).

$repoRoot   = Split-Path $PSScriptRoot -Parent
$psExe      = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
$scriptPath = Join-Path $repoRoot "scripts\open_ppt.ps1"
$psArgs     = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$desc       = "Open latest executive PPT delivery"
$lnkName    = "Executive PPT (Open).lnk"

$shell = New-Object -ComObject WScript.Shell

# ---- Primary Desktop: OneDrive preferred over plain ----
$oneDriveDesktop = Join-Path $env:USERPROFILE "OneDrive\Desktop"
$plainDesktop    = Join-Path $env:USERPROFILE "Desktop"
$primaryDesktop  = if (Test-Path $oneDriveDesktop) { $oneDriveDesktop } else { $plainDesktop }
$lnkPath = Join-Path $primaryDesktop $lnkName

$s = $shell.CreateShortcut($lnkPath)
$s.TargetPath       = $psExe
$s.Arguments        = $psArgs
$s.WorkingDirectory = $repoRoot
$s.Description      = $desc
$s.Save()
Write-Output "SHORTCUT_CREATED: $lnkPath"
Write-Output "TARGET: $psExe $psArgs"
Write-Output "STARTIN: $repoRoot"

# ---- Public Desktop: WARN on failure (non-fatal) ----
$pubLnk = "C:\Users\Public\Desktop\$lnkName"
try {
    $sp = $shell.CreateShortcut($pubLnk)
    $sp.TargetPath       = $psExe
    $sp.Arguments        = $psArgs
    $sp.WorkingDirectory = $repoRoot
    $sp.Description      = $desc
    $sp.Save()
    Write-Output "SHORTCUT_CREATED_PUBLIC: $pubLnk"
} catch {
    Write-Output "WARN: Public Desktop shortcut failed: $_"
}
