# =========================================================
#  AI Intel Scraper — 一鍵啟動腳本（PowerShell）
#  每次執行後將結果歸檔至 outputs\runs\<timestamp>\
# =========================================================

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# 切到專案根目錄
Push-Location (Split-Path $PSScriptRoot -Parent -ErrorAction SilentlyContinue)
if (-not $?) { Push-Location (Join-Path $PSScriptRoot "..") }

try {
    # --- 產生 timestamp ---
    $ts = Get-Date -Format "yyyy-MM-dd_HHmmss"

    # --- 建立歸檔目錄 ---
    $runDir = Join-Path "outputs\runs" $ts
    New-Item -ItemType Directory -Force -Path $runDir | Out-Null

    Write-Host ""
    Write-Host "============================================"
    Write-Host "  AI Intel Scraper — Pipeline Run"
    Write-Host "  Timestamp: $ts"
    Write-Host "============================================"
    Write-Host ""

    # --- 執行管線 ---
    & venv\Scripts\python.exe scripts\run_once.py
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        Write-Host ""
        Write-Host "[ERROR] Pipeline failed with exit code $exitCode." -ForegroundColor Red
        Write-Host "        Skipping archive step."
        exit $exitCode
    }

    # --- 歸檔結果 ---
    $files = @("deep_analysis.md", "metrics.json", "digest.md")

    foreach ($f in $files) {
        $src = Join-Path "outputs" $f
        if (Test-Path $src) {
            Copy-Item -Path $src -Destination (Join-Path $runDir $f) -Force
        }
    }

    # --- write latest_run pointers (atomic: write tmp then move) ---
    $outDir = "outputs"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    $absOutDir = (Resolve-Path $outDir).Path
    $tmpRun = Join-Path $absOutDir "latest_run.txt.tmp"
    $tmpDir = Join-Path $absOutDir "latest_run_dir.txt.tmp"

    $utf8NoBOM = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($tmpRun, "$ts`r`n", $utf8NoBOM)
    [System.IO.File]::WriteAllText($tmpDir, "outputs\runs\$ts\`r`n", $utf8NoBOM)

    Move-Item -Path $tmpRun -Destination (Join-Path $outDir "latest_run.txt") -Force
    Move-Item -Path $tmpDir -Destination (Join-Path $outDir "latest_run_dir.txt") -Force

    # --- 印出結果路徑 ---
    Write-Host ""
    Write-Host "============================================"
    Write-Host "  Run archived to: $runDir\"
    Write-Host "============================================"

    foreach ($f in $files) {
        $src = Join-Path "outputs" $f
        if (Test-Path $src) {
            Write-Host "  Latest: outputs\$f"
        }
    }

    Write-Host "============================================"
    Write-Host ""

} finally {
    Pop-Location
}
