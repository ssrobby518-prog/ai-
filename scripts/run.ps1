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
    Write-Host "  AI Intel Scraper — 管線執行"
    Write-Host "  產生時間: $ts"
    Write-Host "============================================"
    Write-Host ""

    # --- 執行管線 ---
    & venv\Scripts\python.exe scripts\run_once.py
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        Write-Host ""
        Write-Host "[錯誤] 管線執行失敗，代碼 $exitCode。" -ForegroundColor Red
        Write-Host "        已略過歸檔步驟。"
        exit $exitCode
    }

    # --- 歸檔結果 ---
    $files = @("deep_analysis.md", "metrics.json", "digest.md")

    foreach ($f in $files) {
        $src = Join-Path "outputs" $f
        if (Test-Path $src) {
            $srcFull = (Resolve-Path $src).Path
            $destFull = (Join-Path (Resolve-Path $runDir).Path $f)
            [System.IO.File]::Copy($srcFull, $destFull, $true)
        }
    }

    # --- write latest_run pointers ---
    $outDir = "outputs"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    $absOutDir = (Resolve-Path $outDir).Path
    $runPath = Join-Path $absOutDir "latest_run.txt"
    $dirPath = Join-Path $absOutDir "latest_run_dir.txt"

    $utf8NoBOM = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($runPath, "$ts`r`n", $utf8NoBOM)
    [System.IO.File]::WriteAllText($dirPath, "outputs\runs\$ts\`r`n", $utf8NoBOM)

    # --- 印出結果路徑 ---
    Write-Host ""
    Write-Host "============================================"
    Write-Host "  本次歸檔位置: $runDir\"
    Write-Host "============================================"

    foreach ($f in $files) {
        $src = Join-Path "outputs" $f
        if (Test-Path $src) {
            Write-Host "  最新檔案: outputs\$f"
        }
    }

    Write-Host "============================================"
    Write-Host ""

} finally {
    Pop-Location
}
