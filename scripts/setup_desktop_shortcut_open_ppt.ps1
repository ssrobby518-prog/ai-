# setup_desktop_shortcut_open_ppt.ps1
# Create / overwrite "Executive PPT (Open).lnk" on Desktop(s).

$repoRoot   = Split-Path $PSScriptRoot -Parent
$psExe      = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
$scriptPath = Join-Path $repoRoot "scripts\open_ppt.ps1"
$psArgs     = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$desc       = "Open executive PPT report"

$shell = New-Object -ComObject WScript.Shell

# Collect target .lnk paths
$lnkPaths = [System.Collections.Generic.List[string]]::new()
$lnkPaths.Add((Join-Path $env:USERPROFILE "Desktop\Executive PPT (Open).lnk"))
$lnkPaths.Add("C:\Users\Public\Desktop\Executive PPT (Open).lnk")

# OneDrive Desktop (optional)
$oneDriveDesktop = Join-Path $env:USERPROFILE "OneDrive\Desktop"
if (Test-Path $oneDriveDesktop) {
    $lnkPaths.Add((Join-Path $oneDriveDesktop "Executive PPT (Open).lnk"))
}

foreach ($lnkPath in $lnkPaths) {
    try {
        $s = $shell.CreateShortcut($lnkPath)
        $s.TargetPath       = $psExe
        $s.Arguments        = $psArgs
        $s.WorkingDirectory = $repoRoot
        $s.Description      = $desc
        $s.Save()
        Write-Output "SHORTCUT_CREATED: $lnkPath"
        Write-Output "TARGET: $psExe $psArgs"
        Write-Output "STARTIN: $repoRoot"
    } catch {
        Write-Output "SHORTCUT_FAIL: $lnkPath - $_"
    }
}
