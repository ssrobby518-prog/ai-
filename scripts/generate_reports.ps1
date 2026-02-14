# generate_reports.ps1 — One-click executive report generation
# Usage: powershell -ExecutionPolicy Bypass -File scripts\generate_reports.ps1

$ErrorActionPreference = "Stop"

# UTF-8 console hardening
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

Write-Host "=== Executive Report Generator ===" -ForegroundColor Cyan

# Resolve project root
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot

# Prefer venv python if available
$venvPython = Join-Path $projectRoot "venv\Scripts\python.exe"
if (Test-Path $venvPython) { $py = $venvPython } else { $py = "python" }

# Run pipeline
Write-Host "`nRunning analysis pipeline..." -ForegroundColor Yellow
& $py scripts/run_once.py
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Host "Pipeline failed (exit code: $exitCode)" -ForegroundColor Red
    exit 1
}
Write-Host "Pipeline completed successfully." -ForegroundColor Green

# Verify all 4 output files exist and show sizes
Write-Host "`n=== Output Files ===" -ForegroundColor Cyan

$files = @(
    "outputs\executive_report.docx",
    "outputs\executive_report.pptx",
    "outputs\notion_page.md",
    "outputs\mindmap.xmind"
)

$allExist = $true
foreach ($f in $files) {
    $fullPath = Join-Path $projectRoot $f
    if (Test-Path $fullPath) {
        $info = Get-Item $fullPath
        Write-Host ("  {0,-40} {1,10:N0} bytes" -f $info.FullName, $info.Length) -ForegroundColor Green
    } else {
        Write-Host "  MISSING: $fullPath" -ForegroundColor Red
        $allExist = $false
    }
}

if (-not $allExist) {
    Write-Host "`nSome output files are missing!" -ForegroundColor Red
    exit 1
}

Write-Host "`n=== All reports generated successfully ===" -ForegroundColor Cyan
