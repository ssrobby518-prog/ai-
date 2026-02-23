# open_ppt.ps1 — Open canonical executive_report.pptx.
# Always opens outputs\executive_report.pptx.
# Never scans outputs\deliveries or reads pointer files (latest_delivery.json/txt).
# Called by open_latest.ps1 and run_pipeline.ps1 AutoOpen flow.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\open_ppt.ps1

$ErrorActionPreference = 'SilentlyContinue'
$repoRoot  = Split-Path $PSScriptRoot -Parent
$canonical = Join-Path $repoRoot "outputs\executive_report.pptx"

if (Test-Path $canonical) {
    try {
        Start-Process -FilePath $canonical -WindowStyle Normal
        Write-Output "OPEN_PPT: OK $canonical"
        exit 0
    } catch {
        Write-Output "OPEN_PPT: FAIL $_"
        exit 1
    }
} else {
    Write-Output "OPEN_PPT: NOT_FOUND ($canonical)"
    exit 2
}
