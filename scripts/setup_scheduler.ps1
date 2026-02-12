# AI 情報每日排程設定 — Windows Task Scheduler
# 使用方式：powershell scripts\setup_scheduler.ps1
#
# R1 修正：
# - 固定使用 venv\Scripts\python.exe（不回退至系統 Python）
# - 設定工作目錄（StartIn）為專案根目錄
# - 支援重複執行：同名任務存在時覆蓋更新（-Force）
# - 所有輸出訊息為繁體中文

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PythonExe = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$ScriptPath = Join-Path $ProjectRoot "scripts\run_daily.py"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  AI 情報每日排程設定工具" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "專案根目錄：$ProjectRoot"
Write-Host "Python 路徑：$PythonExe"
Write-Host "腳本路徑：  $ScriptPath"
Write-Host ""

# 檢查 venv Python 是否存在（不使用系統 Python 作為回退）
if (-not (Test-Path $PythonExe)) {
    Write-Host "[錯誤] 找不到 venv Python：$PythonExe" -ForegroundColor Red
    Write-Host "[提示] 請先執行：python -m venv venv" -ForegroundColor Yellow
    Write-Host "[提示] 再執行：venv\Scripts\pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

# 檢查腳本是否存在
if (-not (Test-Path $ScriptPath)) {
    Write-Host "[錯誤] 找不到執行腳本：$ScriptPath" -ForegroundColor Red
    exit 1
}

Write-Host "[確認] venv Python 存在" -ForegroundColor Green
Write-Host "[確認] 執行腳本存在" -ForegroundColor Green
Write-Host ""

# 建立排程任務動作（指定工作目錄）
$action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument $ScriptPath `
    -WorkingDirectory $ProjectRoot

# 每日 06:00 觸發
$trigger = New-ScheduledTaskTrigger -Daily -At 6am

# 任務設定
$taskSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

# 註冊任務（-Force 允許覆蓋已存在的同名任務）
Register-ScheduledTask `
    -TaskName "AI_Intel_Daily" `
    -Action $action `
    -Trigger $trigger `
    -Settings $taskSettings `
    -Description "每日 06:00 自動產出 AI 情報摘要與深度分析報告" `
    -Force

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  排程任務已成功註冊！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "任務名稱：AI_Intel_Daily"
Write-Host "執行時間：每日 06:00"
Write-Host "Python：  $PythonExe"
Write-Host "工作目錄：$ProjectRoot"
Write-Host ""
Write-Host "常用指令：" -ForegroundColor Yellow
Write-Host "  查看任務：Get-ScheduledTask -TaskName 'AI_Intel_Daily'"
Write-Host "  立即執行：Start-ScheduledTask -TaskName 'AI_Intel_Daily'"
Write-Host "  移除任務：Unregister-ScheduledTask -TaskName 'AI_Intel_Daily'"
