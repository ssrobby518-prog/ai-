# z0_collect.ps1 — Run Z0 Collector (online AI-news fetch)
# Usage: powershell -ExecutionPolicy Bypass -File scripts\z0_collect.ps1

$ErrorActionPreference = "Stop"

# UTF-8 output
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$repoRoot = Split-Path $PSScriptRoot -Parent

# Prefer venv python
$venvPython = Join-Path $repoRoot "venv\Scripts\python.exe"
if (Test-Path $venvPython) { $py = $venvPython } else { $py = "python" }

$configPath = Join-Path $repoRoot "config\z0_sources.json"
$outDir     = Join-Path $repoRoot "data\raw\z0"

if (-not (Test-Path $configPath)) {
    Write-Output "[z0_collect] ERROR: config not found: $configPath"
    exit 1
}

Write-Output "[z0_collect] Starting Z0 collection..."
Write-Output "[z0_collect] Config: $configPath"
Write-Output "[z0_collect] Output: $outDir"

& $py (Join-Path $repoRoot "core\z0_collector.py") `
    --config $configPath `
    --outdir $outDir

$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    Write-Output "[z0_collect] ERROR: collector exited with code $exitCode"
    exit $exitCode
}

# Print meta summary
$metaPath = Join-Path $outDir "latest.meta.json"
if (Test-Path $metaPath) {
    $meta = Get-Content $metaPath -Raw | ConvertFrom-Json
    Write-Output "[z0_collect] Done: total_items=$($meta.total_items)  frontier_ge_70=$($meta.frontier_ge_70)  frontier_ge_85=$($meta.frontier_ge_85)"
    Write-Output "[z0_collect] collected_at: $($meta.collected_at)"
} else {
    Write-Output "[z0_collect] WARNING: meta file not found after collection"
}
