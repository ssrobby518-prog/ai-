# FILE: scripts\run_pipeline.ps1
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RepoRoot

python main.py
$ExitCode = $LASTEXITCODE

if ($ExitCode -eq 0) {
    $PptScript = Join-Path $RepoRoot "scripts\open_ppt.ps1"
    if (Test-Path $PptScript) {
        & $PptScript
    }
}

exit $ExitCode
