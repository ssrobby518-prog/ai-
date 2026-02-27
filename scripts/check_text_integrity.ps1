# check_text_integrity.ps1 — Repo text consistency checker
# Checks: CRLF in source files, BOM markers, git core.autocrlf warning
# Usage: powershell -ExecutionPolicy Bypass -File scripts\check_text_integrity.ps1
# Exit code 0 = all clear, 1 = issues found

param([switch]$Fix)

$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$repoRoot = Split-Path $PSScriptRoot -Parent
$issues = 0

Write-Host "=== Text Integrity Check ===" -ForegroundColor Cyan

# --- 1. Check git core.autocrlf ---
Write-Host "`n[1/3] Checking git core.autocrlf..." -ForegroundColor Yellow
$autocrlf = git config --get core.autocrlf 2>$null
if ($autocrlf -eq "true") {
    Write-Host "  WARNING: core.autocrlf=true — may cause CRLF/LF mismatch." -ForegroundColor Red
    Write-Host "  Recommended: git config --global core.autocrlf false" -ForegroundColor Yellow
    Write-Host "  (This repo uses .gitattributes eol=lf to enforce LF.)" -ForegroundColor Yellow
    $issues++
} else {
    Write-Host "  OK (core.autocrlf=$autocrlf)" -ForegroundColor Green
}

# --- 2. Check for CRLF in tracked source files ---
Write-Host "`n[2/3] Scanning for CRLF line endings..." -ForegroundColor Yellow
$extensions = @("*.py", "*.md", "*.yml", "*.yaml", "*.toml", "*.ps1", "*.json", "*.cfg", "*.ini", "*.sh", "*.txt")
$crlfFiles = @()

foreach ($ext in $extensions) {
    $files = git ls-files -- $ext 2>$null
    foreach ($f in $files) {
        if (-not $f) { continue }
        $fullPath = Join-Path $repoRoot $f
        if (-not (Test-Path $fullPath)) { continue }
        $bytes = [System.IO.File]::ReadAllBytes($fullPath)
        for ($i = 0; $i -lt $bytes.Length; $i++) {
            if ($bytes[$i] -eq 0x0D -and ($i + 1) -lt $bytes.Length -and $bytes[$i + 1] -eq 0x0A) {
                $crlfFiles += $f
                break
            }
        }
    }
}

if ($crlfFiles.Count -gt 0) {
    Write-Host "  CRLF found in $($crlfFiles.Count) file(s):" -ForegroundColor Red
    foreach ($cf in $crlfFiles) {
        Write-Host "    $cf" -ForegroundColor Red
    }
    if ($Fix) {
        Write-Host "  Fixing with git add --renormalize..." -ForegroundColor Yellow
        git add --renormalize .
        Write-Host "  Done. Review with: git diff --cached" -ForegroundColor Green
    } else {
        Write-Host "  Run with -Fix to auto-repair, or: git add --renormalize ." -ForegroundColor Yellow
    }
    $issues++
} else {
    Write-Host "  OK (no CRLF in tracked source files)" -ForegroundColor Green
}

# --- 3. Check for UTF-8 BOM ---
# .ps1 files are exempt: Windows PowerShell 5.x requires UTF-8 BOM to correctly
# read files that contain non-ASCII characters (em dashes, arrows, CJK, etc.).
# Python/JSON/YAML/TOML/MD/INI/SH/TXT files must NOT have BOM.
Write-Host "`n[3/3] Scanning for UTF-8 BOM markers (exempting .ps1)..." -ForegroundColor Yellow
$bomFiles = @()
$bomExtensions = @("*.py", "*.md", "*.yml", "*.yaml", "*.toml", "*.json", "*.cfg", "*.ini", "*.sh", "*.txt")

foreach ($ext in $bomExtensions) {
    $files = git ls-files -- $ext 2>$null
    foreach ($f in $files) {
        if (-not $f) { continue }
        $fullPath = Join-Path $repoRoot $f
        if (-not (Test-Path $fullPath)) { continue }
        $bytes = [System.IO.File]::ReadAllBytes($fullPath)
        if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
            $bomFiles += $f
        }
    }
}

if ($bomFiles.Count -gt 0) {
    Write-Host "  BOM found in $($bomFiles.Count) file(s):" -ForegroundColor Red
    foreach ($bf in $bomFiles) {
        Write-Host "    $bf" -ForegroundColor Red
    }
    $issues++
} else {
    Write-Host "  OK (no BOM markers)" -ForegroundColor Green
}

# --- Summary ---
Write-Host ""
if ($issues -gt 0) {
    Write-Host "Text integrity: $issues issue(s) found." -ForegroundColor Red
    exit 1
} else {
    Write-Host "Text integrity: ALL CLEAR" -ForegroundColor Green
    exit 0
}
