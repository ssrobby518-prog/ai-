@echo off
setlocal EnableExtensions

REM --- go to repo root (scripts\ -> repo\) ---
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%\.."
if errorlevel 1 (
  echo [ERROR] Failed to cd to repo root.
  exit /b 2
)

REM --- check venv python exists ---
if not exist "venv\Scripts\python.exe" (
  echo [ERROR] venv\Scripts\python.exe not found.
  echo Please run:
  echo   python -m venv venv
  echo   venv\Scripts\python.exe -m pip install -r requirements.txt
  exit /b 2
)

REM --- timestamp via Windows PowerShell full path (more reliable) ---
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
for /f %%i in ('"%PS%" -NoProfile -Command Get-Date -Format yyyy-MM-dd_HHmmss') do set "TS=%%i"

if "%TS%"=="" (
  echo [ERROR] Failed to get timestamp.
  exit /b 3
)

set "RUN_DIR=outputs\runs\%TS%"
if not exist "%RUN_DIR%" mkdir "%RUN_DIR%"

REM --- ensure deps (best effort) ---
venv\Scripts\python.exe -m pip install -r requirements.txt >nul 2>nul

REM --- run pipeline ---
venv\Scripts\python.exe scripts\run_once.py
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo [ERROR] Pipeline failed. Skipping archive.
  exit /b %EXIT_CODE%
)

REM --- archive outputs (copy only if file exists) ---
if exist "outputs\deep_analysis.md" copy /Y "outputs\deep_analysis.md" "%RUN_DIR%\deep_analysis.md" >nul
if exist "outputs\metrics.json"    copy /Y "outputs\metrics.json"    "%RUN_DIR%\metrics.json"    >nul
if exist "outputs\digest.md"       copy /Y "outputs\digest.md"       "%RUN_DIR%\digest.md"       >nul

REM --- write latest_run pointers (atomic: write tmp then move) ---
if not exist "outputs" mkdir "outputs"
> "outputs\latest_run.txt.tmp" echo %TS%
> "outputs\latest_run_dir.txt.tmp" echo outputs\runs\%TS%\
move /Y "outputs\latest_run.txt.tmp" "outputs\latest_run.txt" >nul
move /Y "outputs\latest_run_dir.txt.tmp" "outputs\latest_run_dir.txt" >nul

echo ============================================
echo  Run archived to: %RUN_DIR%\
echo ============================================
echo  Latest: outputs\deep_analysis.md
if exist "outputs\metrics.json" echo  Latest: outputs\metrics.json
if exist "outputs\digest.md"    echo  Latest: outputs\digest.md
echo ============================================

exit /b 0
