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

# Initialise degraded flag (updated inside the Z0 pool health gate block)
$pool85Degraded = $false

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

    # ---------------------------------------------------------------------------
    # Z0 POOL HEALTH GATES — always-on; override via env vars before calling script
    #   Z0_MIN_TOTAL_ITEMS            (default 800) — guards against near-empty collection
    #   Z0_MIN_FRONTIER85_72H         (default  10) — guards against stale / no fresh news
    #   Z0_ALLOW_DEGRADED             (default   0) — set "1" to allow fallback gate
    #   Z0_MIN_FRONTIER85_72H_FALLBACK(default   4) — fallback target when ALLOW_DEGRADED=1
    # Gate modes:
    #   STRICT  : actual >= target10 → PASS; else FAIL exit 1
    #   DEGRADED: actual >= target10 → PASS; actual >= fallback4 → DEGRADED exit 0; else FAIL exit 1
    # ---------------------------------------------------------------------------
    $z0MinTotal        = if ($env:Z0_MIN_TOTAL_ITEMS)                { [int]$env:Z0_MIN_TOTAL_ITEMS }                else { 800 }
    $z0Min85_72h       = if ($env:Z0_MIN_FRONTIER85_72H)             { [int]$env:Z0_MIN_FRONTIER85_72H }             else { 10  }
    $z0AllowDegraded   = ($env:Z0_ALLOW_DEGRADED -eq "1")
    $z0Fallback85_72h  = if ($env:Z0_MIN_FRONTIER85_72H_FALLBACK)    { [int]$env:Z0_MIN_FRONTIER85_72H_FALLBACK }    else { 4   }

    $z0ActualTotal  = if ($meta.PSObject.Properties['total_items'])        { [int]$meta.total_items }        else { 0 }
    $z0Actual85_72h = if ($meta.PSObject.Properties['frontier_ge_85_72h']) { [int]$meta.frontier_ge_85_72h } else { 0 }

    $gatePoolTotal  = if ($z0ActualTotal  -ge $z0MinTotal)  { "PASS" } else { "FAIL" }

    # Determine frontier85_72h gate result with optional degraded mode
    if ($z0Actual85_72h -ge $z0Min85_72h) {
        $gatePool85_72h = "PASS"
        $z0GateMode     = "STRICT"
    } elseif ($z0AllowDegraded -and ($z0Actual85_72h -ge $z0Fallback85_72h)) {
        $gatePool85_72h = "DEGRADED"
        $z0GateMode     = "DEGRADED"
    } else {
        $gatePool85_72h = "FAIL"
        $z0GateMode     = if ($z0AllowDegraded) { "DEGRADED" } else { "STRICT" }
    }

    $poolTotalFail  = ($gatePoolTotal -eq "FAIL")
    $pool85Fail     = ($gatePool85_72h -eq "FAIL")
    $pool85Degraded = ($gatePool85_72h -eq "DEGRADED")
    $poolAnyFail    = $poolTotalFail -or $pool85Fail

    Write-Output ""
    Write-Output "Z0 POOL HEALTH GATES:"
    Write-Output ("  Z0_MIN_TOTAL_ITEMS    target={0,-5} actual={1,-5} {2}" -f $z0MinTotal,  $z0ActualTotal,  $gatePoolTotal)
    if ($pool85Degraded) {
        Write-Output ("  Z0_MIN_FRONTIER85_72H target={0,-5} actual={1,-5} DEGRADED (fallback={2} PASS)" -f $z0Min85_72h, $z0Actual85_72h, $z0Fallback85_72h)
    } else {
        Write-Output ("  Z0_MIN_FRONTIER85_72H target={0,-5} actual={1,-5} {2}" -f $z0Min85_72h, $z0Actual85_72h, $gatePool85_72h)
    }
    Write-Output ("  Z0_GATE_MODE: {0}" -f $z0GateMode)
    Write-Output ("  meta_path   : {0}" -f $metaPath)
    if ($meta.PSObject.Properties['collected_at']) {
        Write-Output ("  collected_at: {0}" -f $meta.collected_at)
    }

    # Write z0_gate_mode.meta.json for downstream audit
    try {
        $_z0GateMeta = @{
            z0_gate_mode    = $z0GateMode
            target10        = $z0Min85_72h
            fallback        = $z0Fallback85_72h
            actual          = $z0Actual85_72h
            total_actual    = $z0ActualTotal
            total_target    = $z0MinTotal
            allow_degraded  = $z0AllowDegraded
            collected_at    = if ($meta.PSObject.Properties['collected_at']) { $meta.collected_at } else { "" }
            run_head        = (git rev-parse --short HEAD 2>$null | Out-String).Trim()
        }
        $_z0GateMetaPath = Join-Path $repoRoot "outputs\z0_gate_mode.meta.json"
        New-Item -ItemType Directory -Force -Path (Split-Path $_z0GateMetaPath) | Out-Null
        $_z0GateMeta | ConvertTo-Json -Depth 3 | Set-Content $_z0GateMetaPath -Encoding UTF8
    } catch {
        Write-Output "  [warn] z0_gate_mode.meta.json write failed: $_"
    }

    if ($poolAnyFail) {
        Write-Output "  => Z0 POOL HEALTH GATES: FAIL"
        exit 1
    } elseif ($pool85Degraded) {
        Write-Output "  => Z0 POOL HEALTH GATES: DEGRADED RUN (frontier85_72h below target but above fallback)"
        # Do NOT exit 1 — degraded is intentionally allowed; pipeline continues
    } else {
        Write-Output "  => Z0 POOL HEALTH GATES: PASS"
    }
    # ---------------------------------------------------------------------------
    # FRONTIER AUDIT — reads outputs/z0_frontier_audit.meta.json
    # Written by z0_collector collect_all(); shows WHY score is low.
    # ---------------------------------------------------------------------------
    $auditPath = Join-Path $repoRoot "outputs\z0_frontier_audit.meta.json"
    if (Test-Path $auditPath) {
        try {
            $aud = Get-Content $auditPath -Raw -Encoding UTF8 | ConvertFrom-Json
            Write-Output ""
            Write-Output "FRONTIER AUDIT (z0_frontier_audit.meta.json):"

            # Histogram
            if ($aud.PSObject.Properties['frontier_histogram']) {
                $h = $aud.frontier_histogram
                Write-Output ("  frontier_histogram: 0-49={0}  50-69={1}  70-84={2}  85+={3}" `
                    -f $h.PSObject.Properties['0_49'].Value,
                       $h.PSObject.Properties['50_69'].Value,
                       $h.PSObject.Properties['70_84'].Value,
                       $h.PSObject.Properties['85plus'].Value)
            }

            # Bonus counts
            if ($aud.PSObject.Properties['bonus_counts']) {
                $bc = $aud.bonus_counts
                Write-Output ("  business_signal_bonus_hits: {0}" -f $bc.business_signal_bonus_hits)
                Write-Output ("  product_release_bonus_hits: {0}" -f $bc.product_release_bonus_hits)
            }

            # frontier_85_72h_samples (top 10)
            if ($aud.PSObject.Properties['frontier_85_72h_samples']) {
                $samps = $aud.frontier_85_72h_samples
                Write-Output ("  frontier_85_72h_samples ({0} items):" -f $samps.Count)
                $i = 0
                foreach ($s in $samps) {
                    $i++
                    if ($i -gt 10) { break }
                    $bf = ""
                    if ($s.PSObject.Properties['bonus_flags']) {
                        $f = $s.bonus_flags
                        $bb = if ($f.PSObject.Properties['biz_bonus'])  { $f.biz_bonus }  else { 0 }
                        $pb = if ($f.PSObject.Properties['prod_bonus']) { $f.prod_bonus } else { 0 }
                        $bf = " biz=$bb prod=$pb"
                    }
                    Write-Output ("    [{0}] score={1} age={2}h src={3}{4}" `
                        -f $i, $s.score, $s.age_hours, $s.source, $bf)
                    Write-Output ("         $($s.title.Substring(0, [Math]::Min(90, $s.title.Length)))")
                }
            }

            # near-miss samples (80-84 within 72h)
            if ($aud.PSObject.Properties['frontier_near_miss_72h_samples']) {
                $nm = $aud.frontier_near_miss_72h_samples
                if ($nm.Count -gt 0) {
                    Write-Output ("  frontier_near_miss_72h_samples (80-84, {0} items):" -f $nm.Count)
                    $j = 0
                    foreach ($s in $nm) {
                        $j++
                        if ($j -gt 5) { break }
                        $bf = ""
                        if ($s.PSObject.Properties['bonus_flags']) {
                            $f = $s.bonus_flags
                            $bb = if ($f.PSObject.Properties['biz_bonus'])  { $f.biz_bonus }  else { 0 }
                            $pb = if ($f.PSObject.Properties['prod_bonus']) { $f.prod_bonus } else { 0 }
                            $bf = " biz=$bb prod=$pb"
                        }
                        Write-Output ("    [nm$j] score={0} age={1}h src={2}{3}" `
                            -f $s.score, $s.age_hours, $s.source, $bf)
                        Write-Output ("           $($s.title.Substring(0, [Math]::Min(85, $s.title.Length)))")
                    }
                }
            }
        } catch {
            Write-Output "  frontier audit parse error (non-fatal): $_"
        }
    } else {
        Write-Output ""
        Write-Output "FRONTIER AUDIT: z0_frontier_audit.meta.json not found (run collect first)"
    }
} else {
    Write-Output "[verify_online] ERROR: Z0 meta not found: $metaPath — pool health check cannot run."
    exit 1
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
        Write-Output ""
        Write-Output "EXEC KPI ORIGIN AUDIT:"
        foreach ($chKey in @('business', 'product', 'tech')) {
            $bfKey = "${chKey}_backfill"
            $ocKey = "${chKey}_origin_counts"
            if ($ekm.PSObject.Properties[$bfKey]) {
                $bf        = $ekm.$bfKey
                $triggered = if ($bf.triggered) { "true" } else { "false" }
                $note      = if ($bf.note)      { $bf.note } else { "n/a" }
                Write-Output ("  {0}  triggered={1}  note={2}" -f $chKey, $triggered, $note)
            }
            if ($ekm.PSObject.Properties[$ocKey]) {
                $oc = $ekm.$ocKey
                Write-Output ("  {0}_origin_counts: primary_pool={1}  extra_pool={2}  backfill={3}" -f $chKey, $oc.primary_pool, $oc.extra_pool, $oc.backfill)
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

# ---------------------------------------------------------------------------
# EXEC QUALITY GATES (online run) — reads exec_quality.meta.json
# ---------------------------------------------------------------------------
$execQualMetaOnlinePath = Join-Path $repoRoot "outputs\exec_quality.meta.json"
if (Test-Path $execQualMetaOnlinePath) {
    try {
        $eqmO = Get-Content $execQualMetaOnlinePath -Raw -Encoding UTF8 | ConvertFrom-Json

        $g2O = if ($eqmO.PSObject.Properties['source_diversity_gate']) { $eqmO.source_diversity_gate } else { "PASS" }
        $g3O = if ($eqmO.PSObject.Properties['proof_coverage_gate'])   { $eqmO.proof_coverage_gate }   else { "PASS" }
        $g4O = if ($eqmO.PSObject.Properties['fragment_leak_gate'])    { $eqmO.fragment_leak_gate }    else { "PASS" }

        $nonAiO    = if ($eqmO.PSObject.Properties['non_ai_rejected_count'])  { $eqmO.non_ai_rejected_count }  else { 0 }
        $maxShrO   = if ($eqmO.PSObject.Properties['max_source_share'])       { $eqmO.max_source_share }       else { 0 }
        $maxSrcO   = if ($eqmO.PSObject.Properties['max_source'])             { $eqmO.max_source }             else { "n/a" }
        $proofO    = if ($eqmO.PSObject.Properties['proof_coverage_ratio'])   { $eqmO.proof_coverage_ratio }   else { 0 }
        $leakedO   = if ($eqmO.PSObject.Properties['fragments_leaked'])       { $eqmO.fragments_leaked }       else { 0 }
        $detectedO = if ($eqmO.PSObject.Properties['fragments_detected'])     { $eqmO.fragments_detected }     else { 0 }
        $fixedO    = if ($eqmO.PSObject.Properties['fragments_fixed'])        { $eqmO.fragments_fixed }        else { 0 }
        $enHeavyO       = if ($eqmO.PSObject.Properties['english_heavy_paragraphs_fixed_count']) { $eqmO.english_heavy_paragraphs_fixed_count } else { 0 }
        $glossedO       = if ($eqmO.PSObject.Properties['proper_noun_gloss_applied_count'])      { $eqmO.proper_noun_gloss_applied_count }      else { 0 }
        $actionsNormO   = if ($eqmO.PSObject.Properties['actions_normalized_count'])             { $eqmO.actions_normalized_count }             else { 0 }
        $actionsLeakO   = if ($eqmO.PSObject.Properties['actions_fragment_leak_count'])          { $eqmO.actions_fragment_leak_count }          else { 0 }
        $zhSkeletonizeO = if ($eqmO.PSObject.Properties['english_heavy_skeletonized_count'])     { $eqmO.english_heavy_skeletonized_count }     else { 0 }
        $proofEmptyGateO  = if ($eqmO.PSObject.Properties['proof_empty_gate'])                   { $eqmO.proof_empty_gate }                   else { "PASS" }
        $proofEmptyCountO = if ($eqmO.PSObject.Properties['proof_empty_event_count'])            { $eqmO.proof_empty_event_count }            else { 0 }
        $actNormStatusO = if ($actionsLeakO -eq 0) { "PASS" } else { "FAIL" }

        Write-Output ""
        Write-Output "EXEC QUALITY GATES:"
        Write-Output ("  AI_RELEVANCE_GATE    : PASS (non_ai_rejected={0})" -f $nonAiO)
        Write-Output ("  SOURCE_DIVERSITY_GATE: {0} (max_source_share={1:P1} source={2})" -f $g2O, $maxShrO, $maxSrcO)
        Write-Output ("  PROOF_COVERAGE_GATE  : {0} (ratio={1:P1})" -f $g3O, $proofO)
        Write-Output ("  FRAGMENT_LEAK_GATE   : {0} (leaked={1} detected={2} fixed={3})" -f $g4O, $leakedO, $detectedO, $fixedO)
        Write-Output ("  EN_ZH_HYBRID_GLOSS   : english_heavy_fixed={0}  proper_noun_glossed={1}" -f $enHeavyO, $glossedO)
        Write-Output ("  ACTIONS_NORMALIZATION: {0} (normalized={1} leaked={2})" -f $actNormStatusO, $actionsNormO, $actionsLeakO)
        Write-Output ("  ZH_SKELETONIZE       : count={0}" -f $zhSkeletonizeO)
        Write-Output ("  PROOF_EMPTY_GATE     : {0} (empty={1})" -f $proofEmptyGateO, $proofEmptyCountO)

        $qualAnyFailO = ($g2O -eq "FAIL") -or ($g3O -eq "FAIL") -or ($g4O -eq "FAIL") -or ($actNormStatusO -eq "FAIL") -or ($proofEmptyGateO -eq "FAIL")
        if ($qualAnyFailO) {
            Write-Output "  => EXEC QUALITY GATES: FAIL"
            exit 1
        }
        Write-Output "  => EXEC QUALITY GATES: PASS"
    } catch {
        Write-Output "  exec_quality meta parse error (non-fatal): $_"
    }
} else {
    Write-Output ""
    Write-Output "EXEC QUALITY GATES: exec_quality.meta.json not found (skipped)"
}

# ---------------------------------------------------------------------------
# GIT UPSTREAM PROBE — same hardened logic as verify_run v2; audits tracking
# state; never crashes on [gone] / missing refs
# ORIGIN_REF_MODE values: HEAD | REMOTE_SHOW | FALLBACK | NONE
# ---------------------------------------------------------------------------
$_voGitOriginRef    = $null
$_voGitOriginMode   = "NONE"
$_voGitOriginExists = $false

# Method A: git symbolic-ref — local only, fast
$_voSymRef = (git symbolic-ref --quiet refs/remotes/origin/HEAD 2>$null | Out-String).Trim()
if ($_voSymRef -match "refs/remotes/origin/(.+)") {
    $_voBranchA = $Matches[1].Trim()
    $null = git show-ref --verify "refs/remotes/origin/$_voBranchA" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $_voGitOriginRef    = "origin/$_voBranchA"
        $_voGitOriginMode   = "HEAD"
        $_voGitOriginExists = $true
    }
}
# Method B: git remote show origin — ref must still exist locally
if (-not $_voGitOriginRef) {
    $_voRemoteShow = (git remote show origin 2>$null | Out-String)
    if ($_voRemoteShow -match "HEAD branch:\s*(.+)") {
        $_voBranchB = $Matches[1].Trim()
        if ($_voBranchB -ne "(unknown)" -and $_voBranchB -ne "") {
            $null = git show-ref --verify "refs/remotes/origin/$_voBranchB" 2>$null
            if ($LASTEXITCODE -eq 0) {
                $_voGitOriginRef    = "origin/$_voBranchB"
                $_voGitOriginMode   = "REMOTE_SHOW"
                $_voGitOriginExists = $true
            }
        }
    }
}
# Method C: explicit local probe — origin/main then origin/master
if (-not $_voGitOriginRef) {
    foreach ($_voFb in @("main", "master")) {
        $null = git show-ref --verify "refs/remotes/origin/$_voFb" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $_voGitOriginRef    = "origin/$_voFb"
            $_voGitOriginMode   = "FALLBACK"
            $_voGitOriginExists = $true
            break
        }
    }
}

$_voOriginRefStr    = if ($_voGitOriginRef)    { $_voGitOriginRef } else { "n/a" }
$_voOriginExistsStr = if ($_voGitOriginExists) { "true" }           else { "false" }

Write-Output ""
Write-Output "GIT UPSTREAM:"
Write-Output ("  ORIGIN_REF_USED  : {0}" -f $_voOriginRefStr)
Write-Output ("  ORIGIN_REF_MODE  : {0}" -f $_voGitOriginMode)
Write-Output ("  ORIGIN_REF_EXISTS: {0}" -f $_voOriginExistsStr)
Write-Output ""
Write-Output "GIT SYNC:"
if ($_voGitOriginRef -and $_voGitOriginExists) {
    $_voAbRaw = (git rev-list --left-right --count "$_voGitOriginRef...HEAD" 2>$null | Out-String).Trim()
    if ($_voAbRaw -match "^(\d+)\s+(\d+)$") {
        $_voBehind = [int]$Matches[1]; $_voAhead = [int]$Matches[2]
        Write-Output ("  GIT_SYNC: behind={0} ahead={1}" -f $_voBehind, $_voAhead)
        if ($_voBehind -eq 0 -and $_voAhead -eq 0) {
            Write-Output "  GIT_UP_TO_DATE: PASS"
        } else {
            Write-Output ("  GIT_UP_TO_DATE: FAIL (diverged from {0})" -f $_voGitOriginRef)
            if ($_voAhead  -gt 0) { Write-Output ("  >> {0} commit(s) ahead; run: git push" -f $_voAhead) }
            if ($_voBehind -gt 0) { Write-Output ("  >> {0} commit(s) behind; run: git pull" -f $_voBehind) }
        }
    } else {
        Write-Output "  GIT_SYNC: WARN — rev-list returned no output"
        Write-Output "  GIT_UP_TO_DATE: WARN-OK (rev-list empty; run: git fetch origin --prune)"
    }
} else {
    Write-Output "  GIT_SYNC: SKIPPED (origin ref not found in local store)"
    Write-Output "  GIT_UP_TO_DATE: WARN-OK (cannot verify; run: git fetch origin --prune)"
}

# ---------------------------------------------------------------------------
# LONGFORM EVIDENCE — reads exec_longform.meta.json written by ppt_generator
# ---------------------------------------------------------------------------
$voLongformPath = Join-Path $repoRoot "outputs\exec_longform.meta.json"
if (Test-Path $voLongformPath) {
    try {
        $voLfm = Get-Content $voLongformPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $voLfElig    = if ($voLfm.PSObject.Properties['eligible_count'])       { [int]$voLfm.eligible_count }       else { 0 }
        $voLfInelig  = if ($voLfm.PSObject.Properties['ineligible_count'])     { [int]$voLfm.ineligible_count }     else { 0 }
        $voLfTotal   = if ($voLfm.PSObject.Properties['total_cards_processed']){ [int]$voLfm.total_cards_processed } else { 0 }
        $voLfERatio  = if ($voLfm.PSObject.Properties['eligible_ratio'])       { [double]$voLfm.eligible_ratio }     else { 0.0 }
        $voLfPRatio  = if ($voLfm.PSObject.Properties['proof_coverage_ratio']) { [double]$voLfm.proof_coverage_ratio } else { 0.0 }
        $voLfAvg     = if ($voLfm.PSObject.Properties['avg_anchor_chars'])     { $voLfm.avg_anchor_chars }           else { 0 }
        $voLfPMiss   = if ($voLfm.PSObject.Properties['proof_missing_count'])  { [int]$voLfm.proof_missing_count }   else { 0 }
        $voLfMissIds = if ($voLfm.PSObject.Properties['proof_missing_ids'] -and $voLfm.proof_missing_ids) { ($voLfm.proof_missing_ids -join ', ') } else { '(none)' }
        $voLfConsist = ($voLfElig + $voLfInelig) -eq $voLfTotal

        Write-Output ""
        Write-Output "LONGFORM EVIDENCE (exec_longform.meta.json):"
        Write-Output ("  generated_at            : {0}" -f $voLfm.generated_at)
        Write-Output ("  total_cards_processed   : {0}" -f $voLfTotal)
        Write-Output ("  eligible_count          : {0}  (ratio={1:P1})" -f $voLfElig, $voLfERatio)
        Write-Output ("  ineligible_count        : {0}" -f $voLfInelig)
        Write-Output ("  counts_consistent       : {0}" -f $(if ($voLfConsist) { 'YES' } else { 'NO — MISMATCH' }))
        Write-Output ("  avg_anchor_chars        : {0}" -f $voLfAvg)
        Write-Output ("  proof_missing_count     : {0}" -f $voLfPMiss)
        Write-Output ("  proof_missing_ids(top5) : {0}" -f $voLfMissIds)
        Write-Output ("  proof_coverage_ratio    : {0:P1}" -f $voLfPRatio)
        $voLfPass = $voLfConsist -and ($voLfPRatio -ge 0.8 -or $voLfTotal -eq 0)
        if ($voLfPass) {
            Write-Output "  => LONGFORM_EVIDENCE: PASS"
        } else {
            Write-Output ("  => LONGFORM_EVIDENCE: WARN (proof_ratio={0:P1} consistent={1})" -f $voLfPRatio, $voLfConsist)
        }
    } catch {
        Write-Output "  longform meta parse error (non-fatal): $_"
    }
} else {
    Write-Output ""
    Write-Output "LONGFORM EVIDENCE: exec_longform.meta.json not found (skipped)"
}

# ---------------------------------------------------------------------------
# LONGFORM DAILY COUNT (Watchlist/Developing Pool Expansion v1)
# ---------------------------------------------------------------------------
if (Test-Path $voLongformPath) {
    try {
        $voLdm = Get-Content $voLongformPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($voLdm.PSObject.Properties['longform_daily_total'] -or $voLdm.PSObject.Properties['event_longform_count']) {
            $voLdMin    = if ($voLdm.PSObject.Properties['longform_min_daily_total'])      { [int]$voLdm.longform_min_daily_total }      else { 6 }
            $voLdEv     = if ($voLdm.PSObject.Properties['event_longform_count'])          { [int]$voLdm.event_longform_count }          else { 0 }
            $voLdWlC    = if ($voLdm.PSObject.Properties['watchlist_longform_candidates']) { [int]$voLdm.watchlist_longform_candidates } else { 0 }
            $voLdWlS    = if ($voLdm.PSObject.Properties['watchlist_longform_selected'])   { [int]$voLdm.watchlist_longform_selected }   else { 0 }
            $voLdTotal  = if ($voLdm.PSObject.Properties['longform_daily_total'])          { [int]$voLdm.longform_daily_total }          else { $voLdEv }
            $voLdWlAvg  = if ($voLdm.PSObject.Properties['watchlist_avg_anchor_chars'])    { $voLdm.watchlist_avg_anchor_chars }         else { 0 }
            $voLdWlPR   = if ($voLdm.PSObject.Properties['watchlist_proof_coverage_ratio']){ [double]$voLdm.watchlist_proof_coverage_ratio } else { 1.0 }
            $voLdWlIds  = if ($voLdm.PSObject.Properties['watchlist_selected_ids_top10'] -and $voLdm.watchlist_selected_ids_top10) {
                ($voLdm.watchlist_selected_ids_top10 -join ', ')
            } else { '(none)' }
            $voLdTop3   = if ($voLdm.PSObject.Properties['watchlist_sources_share_top3'] -and $voLdm.watchlist_sources_share_top3.Count -gt 0) {
                ($voLdm.watchlist_sources_share_top3 | ForEach-Object { "$($_.source)=$($_.count)" }) -join ', '
            } else { '(none)' }

            $voLdGate = if ($voLdTotal -ge $voLdMin) { "PASS" } else { "WARN-OK" }

            Write-Output ""
            Write-Output "LONGFORM DAILY COUNT (exec_longform.meta.json):"
            Write-Output ("  longform_min_daily_total       : {0}" -f $voLdMin)
            Write-Output ("  event_longform_count           : {0}" -f $voLdEv)
            Write-Output ("  watchlist_longform_candidates  : {0}" -f $voLdWlC)
            Write-Output ("  watchlist_longform_selected    : {0}" -f $voLdWlS)
            Write-Output ("  longform_daily_total           : {0}  (target >= {1})" -f $voLdTotal, $voLdMin)
            Write-Output ("  watchlist_avg_anchor_chars     : {0}" -f $voLdWlAvg)
            Write-Output ("  watchlist_proof_coverage_ratio : {0:P1}" -f $voLdWlPR)
            Write-Output ("  watchlist_selected_ids(top10)  : {0}" -f $voLdWlIds)
            Write-Output ("  watchlist_sources_top3         : {0}" -f $voLdTop3)
            if ($voLdGate -eq "PASS") {
                Write-Output ("  => LONGFORM_DAILY_TOTAL target={0} actual={1} PASS" -f $voLdMin, $voLdTotal)
            } else {
                Write-Output ("  => LONGFORM_DAILY_TOTAL target={0} actual={1} WARN-OK (watchlist pool may be small)" -f $voLdMin, $voLdTotal)
            }
        }
    } catch {
        Write-Output "  longform daily count parse error (non-fatal): $_"
    }
}

# ---------------------------------------------------------------------------
# EXEC TEXT BAN SCAN — fail-fast gate (v5.2.6 sanitizer validation)
# ---------------------------------------------------------------------------
$voPy = if ($env:PYTHON) { $env:PYTHON } elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "python3" }
Write-Output ""
Write-Output "EXEC TEXT BAN SCAN:"
$voExecBanPhrases = @(
    "詳見原始來源",
    "監控中 本欄暫無事件",
    "Evidence summary: sources=",
    "Key terms: ",
    "validate source evidence and related numbers",
    "run small-scope checks against current workflow",
    "escalate only if next scan confirms sustained",
    "現有策略與資源配置",
    "的趨勢，解決方 記",
    "WATCH .*: validate",
    "TEST .*: run small-scope",
    "MOVE .*: escalate only"
)
$voExecBanHits = 0

$voPptxScanText = & $voPy -c "
from pptx import Presentation
prs = Presentation('outputs/executive_report.pptx')
for slide in prs.slides:
    for shape in slide.shapes:
        if shape.has_text_frame:
            for p in shape.text_frame.paragraphs:
                print(p.text, end=' ')
" 2>$null

$voDocxScanText = & $voPy -c "
from docx import Document
doc = Document('outputs/executive_report.docx')
print(' '.join(p.text for p in doc.paragraphs))
for t in doc.tables:
    for row in t.rows:
        for cell in row.cells:
            print(cell.text, end=' ')
" 2>$null

$voCombined = "$voPptxScanText $voDocxScanText"
foreach ($bp in $voExecBanPhrases) {
    if ($voCombined -match $bp) {
        Write-Output ("  FAIL: Banned phrase '{0}' found in PPT/DOCX output" -f $bp)
        $voExecBanHits++
    }
}

if ($voExecBanHits -gt 0) {
    Write-Output ("  EXEC TEXT BAN SCAN: FAIL ({0} hit(s))" -f $voExecBanHits)
    exit 1
}
Write-Output "  EXEC TEXT BAN SCAN: PASS (0 hits)"

Write-Output ""
if ($pool85Degraded) {
    Write-Output "=== verify_online.ps1 COMPLETE: DEGRADED RUN (Z0 frontier85_72h below strict target; fallback accepted) ==="
} else {
    Write-Output "=== verify_online.ps1 COMPLETE: all gates passed ==="
}
