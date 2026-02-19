# verify_online.ps1 — Z0 collect (online) then verify pipeline (offline read)
#
# Steps:
#   1) Run z0_collect.ps1  (goes online, writes data/raw/z0/latest.jsonl)
#   2) Set Z0_ENABLED=1 so run_once reads the local JSONL instead of going online
#   3) Run verify_run.ps1  (all 9 gates, reads local JSONL, no outbound traffic)
#
# Usage: powershell -ExecutionPolicy Bypass -File scripts\verify_online.ps1

$ErrorActionPreference = "Stop"

chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$repoRoot = Split-Path $PSScriptRoot -Parent

Write-Output "=== verify_online.ps1 START ==="
Write-Output ""

# ---- Step 1: Z0 online collection ----
Write-Output "[1/3] Running Z0 collector (online)..."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "z0_collect.ps1")
if ($LASTEXITCODE -ne 0) {
    Write-Output "[verify_online] Z0 collect FAILED (exit $LASTEXITCODE). Aborting."
    exit 1
}
Write-Output ""

# Print Z0 by_platform summary
$metaPath = Join-Path $repoRoot "data\raw\z0\latest.meta.json"
if (Test-Path $metaPath) {
    $meta = Get-Content $metaPath -Raw | ConvertFrom-Json
    Write-Output "Z0 COLLECTOR EVIDENCE:"
    Write-Output "  collected_at      : $($meta.collected_at)"
    Write-Output "  total_items       : $($meta.total_items)"
    Write-Output "  frontier_ge_70    : $($meta.frontier_ge_70)"
    Write-Output "  frontier_ge_85    : $($meta.frontier_ge_85)"
    if ($meta.PSObject.Properties['frontier_ge_70_72h']) {
        Write-Output "  frontier_ge_70_72h: $($meta.frontier_ge_70_72h)"
    }
    if ($meta.PSObject.Properties['frontier_ge_85_72h']) {
        Write-Output "  frontier_ge_85_72h: $($meta.frontier_ge_85_72h)"
    }
    if ($meta.PSObject.Properties['fallback_ratio']) {
        Write-Output "  fallback_ratio    : $($meta.fallback_ratio)"
    }
    if ($meta.PSObject.Properties['frontier_ge_85_fallback_count']) {
        Write-Output "  f85_fallback_count: $($meta.frontier_ge_85_fallback_count)"
    }
    if ($meta.PSObject.Properties['frontier_ge_85_fallback_ratio']) {
        Write-Output "  f85_fallback_ratio: $($meta.frontier_ge_85_fallback_ratio)"
    }
    Write-Output "  by_platform:"
    if ($meta.by_platform) {
        $meta.by_platform.PSObject.Properties | Sort-Object Value -Descending | ForEach-Object {
            Write-Output "    $($_.Name): $($_.Value)"
        }
    }
    Write-Output ""

    # Optional Z0 gate: total frontier_ge_85
    if ($env:Z0_MIN_FRONTIER85) {
        $minF85 = [int]$env:Z0_MIN_FRONTIER85
        if ($meta.frontier_ge_85 -lt $minF85) {
            Write-Output "[verify_online] Z0 GATE FAIL: frontier_ge_85=$($meta.frontier_ge_85) < required=$minF85"
            exit 1
        }
        Write-Output "[verify_online] Z0 gate OK: frontier_ge_85=$($meta.frontier_ge_85) >= $minF85"
    }
    # Optional Z0 gate: 72h window frontier_ge_85
    if ($env:Z0_MIN_FRONTIER85_72H) {
        $minF85_72h = [int]$env:Z0_MIN_FRONTIER85_72H
        $actual72h = if ($meta.PSObject.Properties['frontier_ge_85_72h']) { [int]$meta.frontier_ge_85_72h } else { 0 }
        if ($actual72h -lt $minF85_72h) {
            Write-Output "[verify_online] Z0 GATE FAIL: frontier_ge_85_72h=$actual72h < required=$minF85_72h"
            exit 1
        }
        Write-Output "[verify_online] Z0 gate OK: frontier_ge_85_72h=$actual72h >= $minF85_72h"
    }
}

# ---- Step 2: Set Z0_ENABLED so pipeline reads local JSONL ----
Write-Output "[2/3] Setting Z0_ENABLED=1 (pipeline will read local JSONL)..."
$env:Z0_ENABLED = "1"

# ---- Step 3: Run verify_run.ps1 ----
Write-Output "[3/3] Running verify_run.ps1 (offline, reads Z0 JSONL)..."
Write-Output ""
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "verify_run.ps1")
$exitCode = $LASTEXITCODE

$env:Z0_ENABLED = $null

if ($exitCode -ne 0) {
    Write-Output "[verify_online] verify_run.ps1 FAILED (exit $exitCode)."
    exit $exitCode
}

Write-Output ""
Write-Output "=== verify_online.ps1 COMPLETE: all gates passed ==="
