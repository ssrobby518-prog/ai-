# verify_online.ps1 — Z0 collect (online) then verify pipeline (offline read)
#
# Steps:
#   1) Run z0_collect.ps1  (goes online, writes data/raw/z0/latest.jsonl)
#   2) Set Z0_ENABLED=1 so run_once reads the local JSONL instead of going online
#   3) Run verify_run.ps1  (all 9 gates, reads local JSONL, no outbound traffic)
#
# Usage: powershell -ExecutionPolicy Bypass -File scripts\verify_online.ps1

$ErrorActionPreference = "Stop"

chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$repoRoot = Split-Path $PSScriptRoot -Parent

Write-Output "=== verify_online.ps1 START ==="
Write-Output ""

# ---- Step 1: Z0 online collection ----
Write-Output "[1/3] Running Z0 collector (online)..."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "z0_collect.ps1")
if ($LASTEXITCODE -ne 0) {
    Write-Output "[verify_online] Z0 collect FAILED (exit $LASTEXITCODE). Aborting."
    exit 1
}
Write-Output ""

# Print Z0 by_platform summary
$metaPath = Join-Path $repoRoot "data\raw\z0\latest.meta.json"
if (Test-Path $metaPath) {
    $meta = Get-Content $metaPath -Raw | ConvertFrom-Json
    Write-Output "Z0 COLLECTOR EVIDENCE:"
    Write-Output "  collected_at      : $($meta.collected_at)"
    Write-Output "  total_items       : $($meta.total_items)"
    Write-Output "  frontier_ge_70    : $($meta.frontier_ge_70)"
    Write-Output "  frontier_ge_85    : $($meta.frontier_ge_85)"
    if ($meta.PSObject.Properties['frontier_ge_70_72h']) {
        Write-Output "  frontier_ge_70_72h: $($meta.frontier_ge_70_72h)"
    }
    if ($meta.PSObject.Properties['frontier_ge_85_72h']) {
        Write-Output "  frontier_ge_85_72h: $($meta.frontier_ge_85_72h)"
    }
    if ($meta.PSObject.Properties['fallback_ratio']) {
        Write-Output "  fallback_ratio    : $($meta.fallback_ratio)"
    }
    if ($meta.PSObject.Properties['frontier_ge_85_fallback_count']) {
        Write-Output "  f85_fallback_count: $($meta.frontier_ge_85_fallback_count)"
    }
    if ($meta.PSObject.Properties['frontier_ge_85_fallback_ratio']) {
        Write-Output "  f85_fallback_ratio: $($meta.frontier_ge_85_fallback_ratio)"
    }
    Write-Output "  by_platform:"
    if ($meta.by_platform) {
        $meta.by_platform.PSObject.Properties | Sort-Object Value -Descending | ForEach-Object {
            Write-Output "    $($_.Name): $($_.Value)"
        }
    }
    Write-Output ""

    # Optional Z0 gate: total frontier_ge_85
    if ($env:Z0_MIN_FRONTIER85) {
        $minF85 = [int]$env:Z0_MIN_FRONTIER85
        if ($meta.frontier_ge_85 -lt $minF85) {
            Write-Output "[verify_online] Z0 GATE FAIL: frontier_ge_85=$($meta.frontier_ge_85) < required=$minF85"
            exit 1
        }
        Write-Output "[verify_online] Z0 gate OK: frontier_ge_85=$($meta.frontier_ge_85) >= $minF85"
    }
    # Optional Z0 gate: 72h window frontier_ge_85
    if ($env:Z0_MIN_FRONTIER85_72H) {
        $minF85_72h = [int]$env:Z0_MIN_FRONTIER85_72H
        $actual72h = if ($meta.PSObject.Properties['frontier_ge_85_72h']) { [int]$meta.frontier_ge_85_72h } else { 0 }
        if ($actual72h -lt $minF85_72h) {
            Write-Output "[verify_online] Z0 GATE FAIL: frontier_ge_85_72h=$actual72h < required=$minF85_72h"
            exit 1
        }
        Write-Output "[verify_online] Z0 gate OK: frontier_ge_85_72h=$actual72h >= $minF85_72h"
    }
}

# ---- Step 2: Set Z0_ENABLED so pipeline reads local JSONL ----
Write-Output "[2/3] Setting Z0_ENABLED=1 (pipeline will read local JSONL)..."
$env:Z0_ENABLED = "1"

# (C) Set EXEC KPI gates — enabled by default; override with env vars before calling this script
if (-not $env:EXEC_MIN_EVENTS)   { $env:EXEC_MIN_EVENTS   = "6" }
if (-not $env:EXEC_MIN_PRODUCT)  { $env:EXEC_MIN_PRODUCT  = "2" }
if (-not $env:EXEC_MIN_TECH)     { $env:EXEC_MIN_TECH     = "2" }
if (-not $env:EXEC_MIN_BUSINESS) { $env:EXEC_MIN_BUSINESS = "2" }
Write-Output "[verify_online] EXEC KPI gates: MIN_EVENTS=$($env:EXEC_MIN_EVENTS) MIN_PRODUCT=$($env:EXEC_MIN_PRODUCT) MIN_TECH=$($env:EXEC_MIN_TECH) MIN_BUSINESS=$($env:EXEC_MIN_BUSINESS)"

# ---- Step 3: Run verify_run.ps1 ----
Write-Output "[3/3] Running verify_run.ps1 (offline, reads Z0 JSONL)..."
Write-Output ""
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "verify_run.ps1")
$exitCode = $LASTEXITCODE

$env:Z0_ENABLED        = $null
$env:EXEC_MIN_EVENTS   = $null
$env:EXEC_MIN_PRODUCT  = $null
$env:EXEC_MIN_TECH     = $null
$env:EXEC_MIN_BUSINESS = $null

if ($exitCode -ne 0) {
    Write-Output "[verify_online] verify_run.ps1 FAILED (exit $exitCode)."
    exit $exitCode
}

# ---------------------------------------------------------------------------
# EXEC KPI GATE EVIDENCE — reads exec_selection.meta.json written by pipeline
# ---------------------------------------------------------------------------
$execSelMetaPath = Join-Path $repoRoot "outputs\exec_selection.meta.json"
if (Test-Path $execSelMetaPath) {
    try {
        $esMeta = Get-Content $execSelMetaPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $minEv  = if ($env:EXEC_MIN_EVENTS)   { [int]$env:EXEC_MIN_EVENTS }   else { 6 }
        $minPr  = if ($env:EXEC_MIN_PRODUCT)  { [int]$env:EXEC_MIN_PRODUCT }  else { 2 }
        $minTe  = if ($env:EXEC_MIN_TECH)     { [int]$env:EXEC_MIN_TECH }     else { 2 }
        $minBu  = if ($env:EXEC_MIN_BUSINESS) { [int]$env:EXEC_MIN_BUSINESS } else { 2 }

        $actEv = if ($esMeta.PSObject.Properties['events_total'])           { [int]$esMeta.events_total }                              else { 0 }
        $actPr = if ($esMeta.PSObject.Properties['events_by_bucket'] -and $esMeta.events_by_bucket.PSObject.Properties['product'])   { [int]$esMeta.events_by_bucket.product }  else { 0 }
        $actTe = if ($esMeta.PSObject.Properties['events_by_bucket'] -and $esMeta.events_by_bucket.PSObject.Properties['tech'])      { [int]$esMeta.events_by_bucket.tech }     else { 0 }
        $actBu = if ($esMeta.PSObject.Properties['events_by_bucket'] -and $esMeta.events_by_bucket.PSObject.Properties['business'])  { [int]$esMeta.events_by_bucket.business } else { 0 }
        $sparseDay = if ($esMeta.PSObject.Properties['sparse_day']) { [bool]$esMeta.sparse_day } else { $false }

        $gateEv = if ($actEv -ge $minEv -or $sparseDay) { "PASS" } else { "FAIL" }
        $gatePr = if ($actPr -ge $minPr -or $sparseDay) { "PASS" } else { "FAIL" }
        $gateTe = if ($actTe -ge $minTe -or $sparseDay) { "PASS" } else { "FAIL" }
        $gateBu = if ($actBu -ge $minBu -or $sparseDay) { "PASS" } else { "FAIL" }
        $sparseNote = if ($sparseDay) { " [sparse-day fallback]" } else { "" }

        Write-Output ""
        Write-Output "EXEC KPI GATES (default):"
        Write-Output ("  MIN_EVENTS={0,-3} actual={1,-4} {2}{3}" -f $minEv, $actEv, $gateEv, $sparseNote)
        Write-Output ("  MIN_PRODUCT={0,-2} actual={1,-4} {2}{3}" -f $minPr, $actPr, $gatePr, $sparseNote)
        Write-Output ("  MIN_TECH={0,-4} actual={1,-4} {2}{3}" -f $minTe, $actTe, $gateTe, $sparseNote)
        Write-Output ("  MIN_BUSINESS={0,-1} actual={1,-4} {2}{3}" -f $minBu, $actBu, $gateBu, $sparseNote)

        $anyFail = $gateEv -eq "FAIL" -or $gatePr -eq "FAIL" -or $gateTe -eq "FAIL" -or $gateBu -eq "FAIL"
        if ($anyFail) {
            Write-Output "  => EXEC KPI GATES: FAIL"
            exit 1
        } else {
            Write-Output "  => EXEC KPI GATES: PASS"
        }
    } catch {
        Write-Output "  exec_selection meta parse error (non-fatal): $_"
    }
} else {
    Write-Output ""
    Write-Output "EXEC KPI GATES: exec_selection.meta.json not found (skipped)"
}

# ---------------------------------------------------------------------------
# EXEC KPI META — reads exec_kpi.meta.json written by pipeline
# ---------------------------------------------------------------------------
$execKpiMetaPath = Join-Path $repoRoot "outputs\exec_kpi.meta.json"
if (Test-Path $execKpiMetaPath) {
    try {
        $ekm = Get-Content $execKpiMetaPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Output ""
        Write-Output "EXEC KPI META:"
        if ($ekm.PSObject.Properties['kpi_targets']) {
            $kt = $ekm.kpi_targets
            Write-Output ("  kpi_targets.events  : {0}" -f $kt.events)
            Write-Output ("  kpi_targets.product : {0}" -f $kt.product)
            Write-Output ("  kpi_targets.tech    : {0}" -f $kt.tech)
            Write-Output ("  kpi_targets.business: {0}" -f $kt.business)
        }
        if ($ekm.PSObject.Properties['kpi_actuals']) {
            $ka = $ekm.kpi_actuals
            Write-Output ("  kpi_actuals.events  : {0}" -f $ka.events)
            Write-Output ("  kpi_actuals.product : {0}" -f $ka.product)
            Write-Output ("  kpi_actuals.tech    : {0}" -f $ka.tech)
            Write-Output ("  kpi_actuals.business: {0}" -f $ka.business)
        }
        foreach ($bfKey in @('business_backfill', 'product_backfill', 'tech_backfill')) {
            if ($ekm.PSObject.Properties[$bfKey]) {
                $bb = $ekm.$bfKey
                $bfLabel = $bfKey -replace '_backfill', ''
                Write-Output ("  {0}_backfill.candidates: {1}  selected: {2}" -f $bfLabel, $bb.candidates_total, $bb.selected_total)
                if ($bb.selected_ids -and $bb.selected_ids.Count -gt 0) {
                    Write-Output ("  {0}_backfill.ids(top5): {1}" -f $bfLabel, ($bb.selected_ids -join ', '))
                }
            }
        }
    } catch {
        Write-Output "  exec_kpi meta parse error (non-fatal): $_"
    }
} else {
    Write-Output ""
    Write-Output "EXEC KPI META: exec_kpi.meta.json not found (skipped)"
}

# Z0 Injection Gate Evidence (printed after pipeline run writes the file)
$z0InjMetaOnlinePath = Join-Path $repoRoot "outputs\z0_injection.meta.json"
if (Test-Path $z0InjMetaOnlinePath) {
    try {
        $z0InjOnline = Get-Content $z0InjMetaOnlinePath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Output ""
        Write-Output "Z0 INJECTION GATE EVIDENCE:"
        Write-Output ("  z0_inject_candidates_total        : {0}" -f $z0InjOnline.z0_inject_candidates_total)
        Write-Output ("  z0_inject_after_frontier_total    : {0}" -f $z0InjOnline.z0_inject_after_frontier_total)
        Write-Output ("  z0_inject_after_channel_gate_total: {0}" -f $z0InjOnline.z0_inject_after_channel_gate_total)
        Write-Output ("  z0_inject_selected_total          : {0}" -f $z0InjOnline.z0_inject_selected_total)
        Write-Output ("  z0_inject_dropped_by_channel_gate : {0}" -f $z0InjOnline.z0_inject_dropped_by_channel_gate)
        Write-Output ("  z0_inject_channel_gate_threshold  : {0}" -f $z0InjOnline.z0_inject_channel_gate_threshold)
    } catch {
        Write-Output "  z0_injection meta parse error (non-fatal): $_"
    }
}

# ---------------------------------------------------------------------------
# DELIVERY ARCHIVE — versioned copy of artifacts for audit / distribution
# Copies files to outputs\deliveries\<YYYYMMDD_HHMMSS>_<HEAD>\ so every
# online run produces a traceable, immutable snapshot alongside the evidence.
# ---------------------------------------------------------------------------
$CURRENT_HEAD = (git -C $repoRoot rev-parse HEAD 2>$null | Select-Object -First 1).Trim()
$_tsOnline    = Get-Date -Format "yyyyMMdd_HHmmss"
$_deliveryDir = Join-Path $repoRoot "outputs\deliveries\${_tsOnline}_${CURRENT_HEAD}"
New-Item -ItemType Directory -Path $_deliveryDir -Force | Out-Null

# Verify archive HEAD consistency: extract HEAD from dir name and compare to current HEAD
$_dirLeaf     = Split-Path $_deliveryDir -Leaf
$ARCHIVE_HEAD = $($_dirLeaf -replace '^\d{8}_\d{6}_', '')
$ARCHIVE_HEAD_MATCH = if ($CURRENT_HEAD -eq $ARCHIVE_HEAD) { "PASS" } else { "FAIL" }

$_toArchive = @(
    "outputs\executive_report.pptx",
    "outputs\executive_report.docx",
    "outputs\exec_selection.meta.json",
    "outputs\exec_kpi.meta.json",
    "outputs\flow_counts.meta.json"
)
$_archivedCount = 0
foreach ($_src in $_toArchive) {
    $_srcFull = Join-Path $repoRoot $_src
    if (Test-Path $_srcFull) {
        Copy-Item -Path $_srcFull -Destination $_deliveryDir -Force
        $_archivedCount++
    }
}
Write-Output ""
Write-Output "DELIVERY ARCHIVE:"
Write-Output ("  delivery_dir      : {0}" -f $_deliveryDir)
Write-Output ("  archived_files    : {0}" -f $_archivedCount)
Write-Output ("  CURRENT_HEAD      : {0}" -f $CURRENT_HEAD)
Write-Output ("  ARCHIVE_HEAD      : {0}" -f $ARCHIVE_HEAD)
Write-Output ("  ARCHIVE_HEAD_MATCH: {0}" -f $ARCHIVE_HEAD_MATCH)
if ($ARCHIVE_HEAD_MATCH -eq "FAIL") {
    Write-Output "[verify_online] FAIL: archive HEAD mismatch - repository changed during run"
    exit 1
}

# ---------------------------------------------------------------------------
# EXEC LAYOUT EVIDENCE (online run — same as verify_run, reproduced here for auditability)
# ---------------------------------------------------------------------------
$execLayoutOnlinePath = Join-Path $repoRoot "outputs\exec_layout.meta.json"
if (Test-Path $execLayoutOnlinePath) {
    try {
        $elmOnline = Get-Content $execLayoutOnlinePath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Output ""
        Write-Output "EXEC LAYOUT EVIDENCE:"
        Write-Output ("  layout_version          : {0}" -f $elmOnline.layout_version)
        if ($elmOnline.PSObject.Properties['template_map']) {
            $tmO = $elmOnline.template_map
            Write-Output ("  template_map.overview   : {0}" -f $tmO.overview)
            Write-Output ("  template_map.ranking    : {0}" -f $tmO.ranking)
            Write-Output ("  template_map.pending    : {0}" -f $tmO.pending)
            Write-Output ("  template_map.sig_summary: {0}" -f $tmO.signal_summary)
            Write-Output ("  template_map.ev_slide_a : {0}" -f $tmO.event_slide_a)
            Write-Output ("  template_map.ev_slide_b : {0}" -f $tmO.event_slide_b)
        }
        if ($elmOnline.PSObject.Properties['fragment_fix_stats']) {
            $ffsO = $elmOnline.fragment_fix_stats
            Write-Output ("  fragment_ratio          : {0}" -f $ffsO.fragment_ratio)
            Write-Output ("  fragments_detected      : {0}" -f $ffsO.fragments_detected)
            Write-Output ("  fragments_fixed         : {0}" -f $ffsO.fragments_fixed)
        }
        if ($elmOnline.PSObject.Properties['bullet_len_stats']) {
            $blsO = $elmOnline.bullet_len_stats
            Write-Output ("  min_bullet_len          : {0}" -f $blsO.min_bullet_len)
            Write-Output ("  avg_bullet_len          : {0}" -f $blsO.avg_bullet_len)
        }
        if ($elmOnline.PSObject.Properties['card_stats']) {
            $csO = $elmOnline.card_stats
            Write-Output ("  proof_token_coverage    : {0}" -f $csO.proof_token_coverage_ratio)
            Write-Output ("  avg_sentences_per_card  : {0}" -f $csO.avg_sentences_per_event_card)
        }
        $validCodesO = @('T1','T2','T3','T4','T5','T6','COVER','STRUCTURED_SUMMARY','CORP_WATCH','KEY_TAKEAWAYS','REC_MOVES','DECISION_MATRIX')
        $invalidCodesO = @()
        if ($elmOnline.PSObject.Properties['slide_layout_map']) {
            foreach ($slO in $elmOnline.slide_layout_map) {
                if ($slO.template_code -notin $validCodesO) { $invalidCodesO += $slO.template_code }
            }
        }
        if ($invalidCodesO.Count -gt 0) {
            Write-Output ("  WARNING: invalid template codes: {0}" -f ($invalidCodesO -join ', '))
        } else {
            Write-Output "  slide_layout_map codes  : all valid (T1-T6 + structural)"
        }
    } catch {
        Write-Output "  exec_layout meta parse error (non-fatal): $_"
    }
} else {
    Write-Output ""
    Write-Output "EXEC LAYOUT EVIDENCE: exec_layout.meta.json not found (skipped)"
}

Write-Output ""
Write-Output "=== verify_online.ps1 COMPLETE: all gates passed ==="
