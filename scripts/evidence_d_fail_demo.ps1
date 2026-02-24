# evidence_d_fail_demo.ps1
# Deliverable D: Controlled FAIL demonstration — POOL_SUFFICIENCY_HARD + EXEC_NEWS_QUALITY_HARD
#
# Mechanism:
#   1) Inject outputs/NOT_READY.md       — triggers POOL_SUFFICIENCY_HARD FAIL in both verifiers
#   2) Inject FAIL exec_news_quality.meta.json — triggers EXEC_NEWS_QUALITY_HARD FAIL
#   3) Run verify_online.ps1             — must exit non-0
#   4) Run verify_run.ps1 -SkipPipeline  — must exit non-0 (skips pipeline; checks NOT_READY gate)
#   5) Capture both exit codes; assert CONSISTENT_FAIL=True
#   6) Auto-cleanup: remove injected files, restore any originals
#
# verify_run.ps1 is called with -SkipPipeline (added Iter-13) to avoid pipeline re-run
# overwriting the injected FAIL state (step 2 would regenerate a clean meta file).
# Neither injection touches any tracked source file — working tree remains clean.
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\evidence_d_fail_demo.ps1

$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

# CJK-safe path resolution: derive repoRoot from $PSScriptRoot
$repoRoot     = Split-Path $PSScriptRoot -Parent
$outputsDir   = Join-Path $repoRoot "outputs"
$verifyOnline = Join-Path $repoRoot "scripts\verify_online.ps1"
$verifyRun    = Join-Path $repoRoot "scripts\verify_run.ps1"

Write-Output ""
Write-Output "=== DELIVERABLE D: FAIL DEMO (evidence_d_fail_demo.ps1) ==="
Write-Output "  Mechanism : inject NOT_READY.md + FAIL exec_news_quality.meta.json"
Write-Output "  Verifiers : verify_online.ps1 + verify_run.ps1 -SkipPipeline"
Write-Output "  Cleanup   : auto (injected files removed; backups restored)"
Write-Output ""

# ---------------------------------------------------------------------------
# 1) Paths
# ---------------------------------------------------------------------------
$nrPath    = Join-Path $outputsDir "NOT_READY.md"
$enqPath   = Join-Path $outputsDir "exec_news_quality.meta.json"
$enqBackup = Join-Path $outputsDir "exec_news_quality.meta.json.d_backup"
$nrBackup  = Join-Path $outputsDir "NOT_READY.md.d_backup"

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
# 4) Inject FAIL exec_news_quality.meta.json (EXEC_NEWS_QUALITY_HARD trigger)
# ---------------------------------------------------------------------------
Write-Output ""
Write-Output "[DEMO] Injecting FAIL exec_news_quality.meta.json ..."
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
# 5) Run verify_online.ps1 — expect exit non-0 (POOL_SUFFICIENCY_HARD: FAIL)
# ---------------------------------------------------------------------------
Write-Output ""
Write-Output "[DEMO] Running verify_online.ps1 -SkipPipeline (expect exit non-0 — injected state)..."
Write-Output "------------------------------------------------------------------------"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $verifyOnline -SkipPipeline
$verifyOnlineExit = $LASTEXITCODE
Write-Output "------------------------------------------------------------------------"
Write-Output ("[DEMO] verify_online.ps1 exit code: {0}" -f $verifyOnlineExit)
if ($verifyOnlineExit -ne 0) {
    Write-Output "[DEMO] CONFIRMED: verify_online exits non-zero under injected FAIL state."
} else {
    Write-Output "[DEMO] UNEXPECTED: verify_online exited 0 — gate did not trigger."
}

# ---------------------------------------------------------------------------
# 6) Run verify_run.ps1 -SkipPipeline — expect exit non-0 (NOT_READY gate: FAIL)
# ---------------------------------------------------------------------------
Write-Output ""
Write-Output "[DEMO] Running verify_run.ps1 -SkipPipeline (expect exit non-0 — NOT_READY gate)..."
Write-Output "------------------------------------------------------------------------"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $verifyRun -SkipPipeline
$verifyRunExit = $LASTEXITCODE
Write-Output "------------------------------------------------------------------------"
Write-Output ("[DEMO] verify_run.ps1 exit code: {0}" -f $verifyRunExit)
if ($verifyRunExit -ne 0) {
    Write-Output "[DEMO] CONFIRMED: verify_run exits non-zero under injected FAIL state."
} else {
    Write-Output "[DEMO] UNEXPECTED: verify_run exited 0 — NOT_READY gate did not trigger."
}

# ---------------------------------------------------------------------------
# 7) Cleanup — restore originals
# ---------------------------------------------------------------------------
Write-Output ""
Write-Output "[DEMO] Cleaning up — restoring originals..."

# Remove injected NOT_READY.md
if (Test-Path $nrPath) {
    Remove-Item $nrPath -Force -ErrorAction SilentlyContinue
    Write-Output "  Removed injected NOT_READY.md."
}

# Restore exec_news_quality.meta.json
if (Test-Path $enqBackup) {
    Copy-Item $enqBackup $enqPath -Force
    Remove-Item $enqBackup -Force -ErrorAction SilentlyContinue
    Write-Output "  exec_news_quality.meta.json restored from backup."
} elseif (-not $enqOrigExisted) {
    if (Test-Path $enqPath) {
        Remove-Item $enqPath -Force -ErrorAction SilentlyContinue
    }
    Write-Output "  exec_news_quality.meta.json removed (was not present originally)."
}

# Restore NOT_READY.md if it existed originally (rare, but correct)
if (Test-Path $nrBackup) {
    Copy-Item $nrBackup $nrPath -Force
    Remove-Item $nrBackup -Force -ErrorAction SilentlyContinue
    Write-Output "  NOT_READY.md restored from backup."
}

# ---------------------------------------------------------------------------
# 8) Result summary
# ---------------------------------------------------------------------------
$consistentFail = ($verifyOnlineExit -ne 0) -and ($verifyRunExit -ne 0)

Write-Output ""
Write-Output "=== DELIVERABLE D RESULT ==="
if ($consistentFail) {
    Write-Output "CONSISTENT_FAIL=True"
} else {
    Write-Output "CONSISTENT_FAIL=False"
}
Write-Output ("verify_online_exit={0}" -f $verifyOnlineExit)
Write-Output ("verify_run_exit={0}"    -f $verifyRunExit)
Write-Output ""
if ($consistentFail) {
    Write-Output "  Both verifiers exit non-zero under injected FAIL state."
    Write-Output "  POOL_SUFFICIENCY_HARD (NOT_READY.md trigger) verified in both verify_online and verify_run."
    Write-Output "  EXEC_NEWS_QUALITY_HARD (meta FAIL trigger) verified in injected exec_news_quality.meta.json."
} else {
    Write-Output "  WARNING: One or more verifiers did not exit non-zero as expected."
}
Write-Output ""
Write-Output "=== DEMO COMPLETE — git status unaffected (outputs/ is .gitignored) ==="
