# evidence_d_fail_demo.ps1
# Deliverable D: Controlled FAIL demonstration for EXEC_NEWS_QUALITY_HARD gate
#
# Mechanism: inject outputs/NOT_READY.md (triggers POOL_SUFFICIENCY_HARD FAIL in
# verify_online.ps1 line 423-426) and a FAIL exec_news_quality.meta.json (triggers
# EXEC_NEWS_QUALITY_HARD FAIL in verify_online.ps1 line 755-764).
# Neither injection touches any tracked source file — working tree stays clean.
#
# NOTE on verify_run.ps1: verify_run.ps1 is NOT called here because it runs
# scripts/run_once.py (the full pipeline, ~10-15 min) at step 2, which regenerates
# exec_news_quality.meta.json from scratch (overwriting any injected FAIL state).
# verify_run.ps1 step 1 also explicitly deletes NOT_READY.md before the pipeline
# runs, making NOT_READY injection ineffective. Therefore the consistent FAIL
# behaviour is demonstrated via verify_online.ps1, which reads the same gate files
# without re-running the pipeline.
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\evidence_d_fail_demo.ps1

$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

# CJK-safe path resolution: derive repoRoot from $PSScriptRoot
$repoRoot   = Split-Path $PSScriptRoot -Parent
$outputsDir = Join-Path $repoRoot "outputs"
$verifyOnline = Join-Path $repoRoot "scripts\verify_online.ps1"

Write-Output ""
Write-Output "=== DELIVERABLE D: FAIL DEMO (evidence_d_fail_demo.ps1) ==="
Write-Output "  Mechanism : inject NOT_READY.md + FAIL exec_news_quality.meta.json"
Write-Output "  Cleanup   : auto (injected files removed; backups restored)"
Write-Output ""

# ---------------------------------------------------------------------------
# 1) Paths
# ---------------------------------------------------------------------------
$nrPath       = Join-Path $outputsDir "NOT_READY.md"
$enqPath      = Join-Path $outputsDir "exec_news_quality.meta.json"
$enqBackup    = Join-Path $outputsDir "exec_news_quality.meta.json.d_backup"
$nrBackup     = Join-Path $outputsDir "NOT_READY.md.d_backup"

$nrOrigExisted  = Test-Path $nrPath
$enqOrigExisted = Test-Path $enqPath

# ---------------------------------------------------------------------------
# 2) Backup originals
# ---------------------------------------------------------------------------
Write-Output "[DEMO] Backing up originals..."
if ($nrOrigExisted) {
    Copy-Item $nrPath $nrBackup -Force
    Write-Output "  Backed up NOT_READY.md"
}
if ($enqOrigExisted) {
    Copy-Item $enqPath $enqBackup -Force
    Write-Output "  Backed up exec_news_quality.meta.json"
}

# ---------------------------------------------------------------------------
# 3) Inject NOT_READY.md (POOL_SUFFICIENCY_HARD trigger)
# ---------------------------------------------------------------------------
Write-Output ""
Write-Output "[DEMO] Injecting outputs/NOT_READY.md ..."
$nrContent = @"
# NOT_READY

run_id: DEMO_INJECTION
gate: EXEC_NEWS_QUALITY_HARD
events_failing: 3

## Failing events (verbatim quote check):
- [DEMO] Event A: failed=['QUOTE_QUALITY']
- [DEMO] Event B: failed=['QUOTE_QUALITY']
- [DEMO] Event C: failed=['QUOTE_QUALITY']

## Fix
This file was injected by evidence_d_fail_demo.ps1 to demonstrate the FAIL gate.
Ensure each selected event's full_text contains >=2 verbatim quotes (>=20 chars, >=4 words each).
"@
[System.IO.File]::WriteAllText($nrPath, $nrContent, [System.Text.Encoding]::UTF8)
Write-Output "  NOT_READY.md injected."

# ---------------------------------------------------------------------------
# 4) Run verify_online.ps1 — expect exit 1 (POOL_SUFFICIENCY_HARD: FAIL)
# ---------------------------------------------------------------------------
Write-Output ""
Write-Output "[DEMO] Running verify_online.ps1 (expect exit 1 — POOL_SUFFICIENCY_HARD FAIL)..."
Write-Output "------------------------------------------------------------------------"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $verifyOnline
$exitCode1 = $LASTEXITCODE
Write-Output "------------------------------------------------------------------------"
Write-Output ("[DEMO] verify_online.ps1 exit code: {0}" -f $exitCode1)
if ($exitCode1 -ne 0) {
    Write-Output "[DEMO] CONFIRMED: verify_online exits non-zero with NOT_READY.md present."
} else {
    Write-Output "[DEMO] UNEXPECTED: verify_online exited 0 — gate did not trigger."
}

# ---------------------------------------------------------------------------
# 5) Remove injected NOT_READY.md; inject FAIL exec_news_quality.meta.json
# ---------------------------------------------------------------------------
Write-Output ""
Write-Output "[DEMO] Removing NOT_READY.md; injecting FAIL exec_news_quality.meta.json..."
Remove-Item $nrPath -Force -ErrorAction SilentlyContinue

$enqFail = @"
{
  "generated_at": "2026-02-24T00:00:00+00:00",
  "events_total": 3,
  "pass_count": 0,
  "fail_count": 3,
  "gate_result": "FAIL",
  "events": [
    {
      "item_id": "demo_a",
      "title": "[DEMO] Event A — injected failure",
      "final_url": "https://example.com/demo_a",
      "quote_1": "",
      "quote_2": "",
      "q1_snippet": "[DEMO placeholder]",
      "q2_snippet": "[DEMO placeholder]",
      "dod": {
        "QUOTE_QUALITY": false,
        "QUOTE_SOURCE": false,
        "QUOTE_NOT_TRIVIAL": false,
        "Q1_BINDING": false,
        "Q2_BINDING": false
      },
      "all_pass": false
    },
    {
      "item_id": "demo_b",
      "title": "[DEMO] Event B — injected failure",
      "final_url": "https://example.com/demo_b",
      "quote_1": "",
      "quote_2": "",
      "q1_snippet": "[DEMO placeholder]",
      "q2_snippet": "[DEMO placeholder]",
      "dod": {
        "QUOTE_QUALITY": false,
        "QUOTE_SOURCE": false,
        "QUOTE_NOT_TRIVIAL": false,
        "Q1_BINDING": false,
        "Q2_BINDING": false
      },
      "all_pass": false
    },
    {
      "item_id": "demo_c",
      "title": "[DEMO] Event C — injected failure",
      "final_url": "https://example.com/demo_c",
      "quote_1": "",
      "quote_2": "",
      "q1_snippet": "[DEMO placeholder]",
      "q2_snippet": "[DEMO placeholder]",
      "dod": {
        "QUOTE_QUALITY": false,
        "QUOTE_SOURCE": false,
        "QUOTE_NOT_TRIVIAL": false,
        "Q1_BINDING": false,
        "Q2_BINDING": false
      },
      "all_pass": false
    }
  ]
}
"@
[System.IO.File]::WriteAllText($enqPath, $enqFail, [System.Text.Encoding]::UTF8)
Write-Output "  exec_news_quality.meta.json injected with gate_result=FAIL."

# ---------------------------------------------------------------------------
# 6) Run verify_online.ps1 again — expect exit 1 (EXEC_NEWS_QUALITY_HARD: FAIL)
# ---------------------------------------------------------------------------
Write-Output ""
Write-Output "[DEMO] Running verify_online.ps1 (expect exit 1 — EXEC_NEWS_QUALITY_HARD FAIL)..."
Write-Output "------------------------------------------------------------------------"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $verifyOnline
$exitCode2 = $LASTEXITCODE
Write-Output "------------------------------------------------------------------------"
Write-Output ("[DEMO] verify_online.ps1 exit code: {0}" -f $exitCode2)
if ($exitCode2 -ne 0) {
    Write-Output "[DEMO] CONFIRMED: verify_online exits non-zero with FAIL exec_news_quality.meta.json."
} else {
    Write-Output "[DEMO] UNEXPECTED: verify_online exited 0 — gate did not trigger."
}

# ---------------------------------------------------------------------------
# 7) Cleanup — restore originals
# ---------------------------------------------------------------------------
Write-Output ""
Write-Output "[DEMO] Cleaning up — restoring originals..."

# Remove injected files
if (Test-Path $nrPath) {
    Remove-Item $nrPath -Force -ErrorAction SilentlyContinue
}

# Restore exec_news_quality.meta.json
if (Test-Path $enqBackup) {
    Copy-Item $enqBackup $enqPath -Force
    Remove-Item $enqBackup -Force -ErrorAction SilentlyContinue
    Write-Output "  exec_news_quality.meta.json restored from backup."
} elseif (-not $enqOrigExisted) {
    Remove-Item $enqPath -Force -ErrorAction SilentlyContinue
    Write-Output "  exec_news_quality.meta.json removed (was not present originally)."
}

# Restore NOT_READY.md if it existed originally
if (Test-Path $nrBackup) {
    Copy-Item $nrBackup $nrPath -Force
    Remove-Item $nrBackup -Force -ErrorAction SilentlyContinue
    Write-Output "  NOT_READY.md restored from backup."
}

Write-Output ""
Write-Output "=== DELIVERABLE D RESULT ==="
$failedBoth = ($exitCode1 -ne 0) -and ($exitCode2 -ne 0)
Write-Output ("  verify_online [NOT_READY injection]    exit={0}  => {1}" -f $exitCode1, (if ($exitCode1 -ne 0) {"FAIL (as expected)"} else {"UNEXPECTED PASS"}))
Write-Output ("  verify_online [ENQ FAIL injection]     exit={0}  => {1}" -f $exitCode2, (if ($exitCode2 -ne 0) {"FAIL (as expected)"} else {"UNEXPECTED PASS"}))
Write-Output ""
if ($failedBoth) {
    Write-Output "CONSISTENT_FAIL=True"
    Write-Output "  Both gate injections independently cause verify_online to exit non-zero."
    Write-Output "  POOL_SUFFICIENCY_HARD gate (NOT_READY.md trigger) and"
    Write-Output "  EXEC_NEWS_QUALITY_HARD gate (meta FAIL trigger) are both verified."
} else {
    Write-Output "CONSISTENT_FAIL=False (one or more gates did not trigger correctly)"
}
Write-Output ""
Write-Output "=== DEMO COMPLETE — git status unaffected (outputs/ is .gitignored) ==="
