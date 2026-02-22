# FILE: scripts\setup_scheduler.bat
@echo off
setlocal

schtasks /Create /TN "AI_Scraper_Daily" /TR "powershell.exe -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File \"C:\Projects\ai捕捉資訊\ai-intel-scraper-mvp\scripts\run_pipeline.ps1\"" /SC DAILY /ST 09:00 /RL HIGHEST /F

if %ERRORLEVEL% NEQ 0 (
    echo ERROR: schtasks failed with exit code %ERRORLEVEL%.
    exit /b %ERRORLEVEL%
)

echo Scheduled task "AI_Scraper_Daily" created: daily 09:00, highest privilege, hidden.
endlocal
