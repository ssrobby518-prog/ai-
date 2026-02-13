# verify_run.ps1 — 教育版報告端到端驗收腳本
# 用途：以 calibration profile 跑 pipeline，驗證 FILTER_SUMMARY + Z5 教育版輸出
# 執行方式：powershell -ExecutionPolicy Bypass -File scripts\verify_run.ps1

$ErrorActionPreference = "Stop"
Write-Host "=== 驗收腳本開始 ===" -ForegroundColor Cyan

# 1) 刪除舊教育版輸出檔
Write-Host "`n[1/5] 刪除舊教育版輸出檔..." -ForegroundColor Yellow
$filesToRemove = @(
    "docs\reports\deep_analysis_education_version.md",
    "docs\reports\deep_analysis_education_version_ppt.md",
    "docs\reports\deep_analysis_education_version_xmind.md",
    "outputs\deep_analysis_education.md"
)
foreach ($f in $filesToRemove) {
    if (Test-Path $f) {
        Remove-Item $f -Force
        Write-Host "  已刪除: $f"
    }
}

# 2) 以 calibration 跑一次 pipeline
Write-Host "`n[2/5] 以 RUN_PROFILE=calibration 執行 pipeline..." -ForegroundColor Yellow
$env:RUN_PROFILE = "calibration"
python scripts/run_once.py
$exitCode = $LASTEXITCODE
$env:RUN_PROFILE = $null  # 清除環境變數

if ($exitCode -ne 0) {
    Write-Host "  Pipeline 執行失敗 (exit code: $exitCode)" -ForegroundColor Red
    exit 1
}
Write-Host "  Pipeline 執行成功" -ForegroundColor Green

# 3) 驗證 FILTER_SUMMARY 存在於 log
Write-Host "`n[3/5] 驗證 FILTER_SUMMARY log..." -ForegroundColor Yellow
$filterLog = Select-String -Path "logs\app.log" -Pattern "FILTER_SUMMARY" -SimpleMatch | Select-Object -Last 1
if ($filterLog) {
    Write-Host "  FILTER_SUMMARY 命中:" -ForegroundColor Green
    Write-Host "  $($filterLog.Line)"
} else {
    Write-Host "  未找到 FILTER_SUMMARY log 行" -ForegroundColor Red
    exit 1
}

# 4) 驗證教育版報告已生成
Write-Host "`n[4/5] 檢查教育版報告檔案..." -ForegroundColor Yellow
$eduFile = "docs\reports\deep_analysis_education_version.md"
if (Test-Path $eduFile) {
    Get-Item $eduFile | Format-List FullName, LastWriteTime, Length
} else {
    Write-Host "  教育版報告不存在: $eduFile" -ForegroundColor Red
    exit 1
}

# 5) 驗證教育版報告內容包含關鍵區塊
Write-Host "[5/5] 驗證教育版報告內容..." -ForegroundColor Yellow
$patterns = @("封面資訊", "今日結論", "Metrics", "系統流程圖", "排錯指引")
$hits = Select-String -Path $eduFile -Pattern ($patterns -join "|") -SimpleMatch
if ($hits.Count -ge 3) {
    Write-Host "  內容驗證通過 ($($hits.Count) 個關鍵區塊命中)" -ForegroundColor Green
} else {
    Write-Host "  內容驗證不足 (僅 $($hits.Count) 個命中)" -ForegroundColor Red
}

# 也檢查空報告特徵（0 items 時）
$emptyHit = Select-String -Path $eduFile -Pattern "本次無有效新聞|篩選原因|今日新聞卡片" -SimpleMatch
if ($emptyHit) {
    Write-Host "  空/非空報告區塊確認:" -ForegroundColor Green
    foreach ($h in $emptyHit) {
        Write-Host "    $($h.Line.Trim().Substring(0, [Math]::Min(80, $h.Line.Trim().Length)))"
    }
}

Write-Host "`n=== 驗收腳本完成 ===" -ForegroundColor Cyan
