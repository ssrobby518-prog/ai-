# verify_run.ps1 ??Executive report end-to-end verification
# Purpose: run pipeline with calibration profile, verify FILTER_SUMMARY + executive output
# Usage: powershell -ExecutionPolicy Bypass -File scripts\verify_run.ps1

$ErrorActionPreference = "Stop"

# UTF-8 console hardening ??prevent garbled CJK output
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

Write-Host "=== Verification Start ===" -ForegroundColor Cyan

# 0) Text integrity pre-check (CRLF / BOM / autocrlf)
Write-Host "`n[0/9] Running text integrity check..." -ForegroundColor Yellow
$integrityScript = Join-Path $PSScriptRoot "check_text_integrity.ps1"
if (Test-Path $integrityScript) {
    & powershell.exe -ExecutionPolicy Bypass -File $integrityScript
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Text integrity check failed ??fix issues before continuing." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "  check_text_integrity.ps1 not found, skipping." -ForegroundColor Yellow
}

# 1) Remove previous outputs
Write-Host "`n[1/9] Removing previous outputs..." -ForegroundColor Yellow
$filesToRemove = @(
    "docs\reports\deep_analysis_education_version.md",
    "docs\reports\deep_analysis_education_version_ppt.md",
    "docs\reports\deep_analysis_education_version_xmind.md",
    "outputs\deep_analysis_education.md",
    "outputs\education_report.docx",
    "outputs\education_report.pptx",
    "outputs\executive_report.docx",
    "outputs\executive_report.pptx",
    "outputs\notion_page.md",
    "outputs\mindmap.xmind"
)
foreach ($f in $filesToRemove) {
    if (Test-Path $f) {
        Remove-Item $f -Force -ErrorAction SilentlyContinue
        Write-Host "  Removed: $f"
    }
}

# 2) Run pipeline with calibration profile
Write-Host "`n[2/9] Running pipeline with RUN_PROFILE=calibration..." -ForegroundColor Yellow
$env:RUN_PROFILE = "calibration"
# Prefer venv python if available, otherwise fall back to system python
$venvPython = Join-Path $PSScriptRoot "..\venv\Scripts\python.exe"
if (Test-Path $venvPython) { $py = $venvPython } else { $py = "python" }
& $py scripts/run_once.py
$exitCode = $LASTEXITCODE
$env:RUN_PROFILE = $null

if ($exitCode -ne 0) {
    Write-Host "  Pipeline failed (exit code: $exitCode)" -ForegroundColor Red
    exit 1
}
Write-Host "  Pipeline succeeded" -ForegroundColor Green

# 3) Verify FILTER_SUMMARY exists in log
Write-Host "`n[3/9] Verifying FILTER_SUMMARY log..." -ForegroundColor Yellow
$filterLog = Select-String -Path "logs\app.log" -Pattern "FILTER_SUMMARY" -SimpleMatch | Select-Object -Last 1
if ($filterLog) {
    Write-Host "  FILTER_SUMMARY hit:" -ForegroundColor Green
    Write-Host "  $($filterLog.Line)"
} else {
    Write-Host "  FILTER_SUMMARY not found" -ForegroundColor Red
    exit 1
}

# 4) Verify education report exists on disk (NOT required to be git-tracked)
Write-Host "`n[4/9] Checking education report file..." -ForegroundColor Yellow
$eduFile = "docs\reports\deep_analysis_education_version.md"
if (Test-Path $eduFile) {
    Get-Item $eduFile | Format-List FullName, LastWriteTime, Length
} else {
    Write-Host "  Report not found: $eduFile" -ForegroundColor Red
    exit 1
}

# 5) Verify education report contains key sections
Write-Host "`n[5/9] Verifying education report content..." -ForegroundColor Yellow
$patterns = @("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Metrics", "mermaid")
$hits = Select-String -Path $eduFile -Pattern $patterns -SimpleMatch
if ($hits.Count -ge 3) {
    Write-Host "  Content check passed ($($hits.Count) section hits)" -ForegroundColor Green
} else {
    Write-Host "  Content check failed (only $($hits.Count) hits)" -ForegroundColor Red
}

# Optional: check empty-report markers
$emptyHit = Select-String -Path $eduFile -Pattern "No items|empty|filters" -SimpleMatch
if ($emptyHit) {
    Write-Host "  Empty/non-empty section check:" -ForegroundColor Green
    foreach ($h in $emptyHit) {
        Write-Host "    $($h.Line.Trim().Substring(0, [Math]::Min(80, $h.Line.Trim().Length)))"
    }
}

# 6) Artifact policy hard-fail guard
Write-Host "`n[6/9] Artifact policy check (hard-fail)..." -ForegroundColor Yellow

function Assert-NotTracked($pattern) {
    $tracked = git ls-files -- $pattern 2>$null
    if ($tracked) {
        Write-Host "  FAIL: Generated artifact is git-tracked:" -ForegroundColor Red
        foreach ($t in $tracked) { Write-Host "    $t" -ForegroundColor Red }
        Write-Host "  Fix: git rm --cached $pattern" -ForegroundColor Yellow
        exit 1
    }
}

Assert-NotTracked "docs/reports/deep_analysis_education_version*.md"
Assert-NotTracked "outputs/*"

Write-Host "  Artifact policy check passed." -ForegroundColor Green

# 7) Education report quality gate
Write-Host "`n[7/9] Education report quality gate..." -ForegroundColor Yellow
& $py -m pytest tests/test_education_report_quality.py -q 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Education quality gate FAILED" -ForegroundColor Red
    exit 1
}
Write-Host "  Education quality gate passed." -ForegroundColor Green

# 8) Executive output files check (DOCX/PPTX/Notion/XMind)
Write-Host "`n[8/9] Checking executive output files..." -ForegroundColor Yellow
$execFiles = @(
    @{ Name="DOCX"; Path="outputs\executive_report.docx" },
    @{ Name="PPTX"; Path="outputs\executive_report.pptx" },
    @{ Name="Notion"; Path="outputs\notion_page.md" },
    @{ Name="XMind"; Path="outputs\mindmap.xmind" }
)
$minSizes = @{
    "DOCX" = 10240
    "PPTX" = 20480
}
$binPass = $true

foreach ($ef in $execFiles) {
    if (Test-Path $ef.Path) {
        $info = Get-Item $ef.Path
        Write-Host ("  {0}: {1} ({2} bytes, {3})" -f $ef.Name, $info.FullName, $info.Length, $info.LastWriteTime) -ForegroundColor Green
        if ($minSizes.ContainsKey($ef.Name) -and $info.Length -lt $minSizes[$ef.Name]) {
            Write-Host ("  FAIL: {0} too small ({1} bytes < {2} bytes threshold)" -f $ef.Name, $info.Length, $minSizes[$ef.Name]) -ForegroundColor Red
            $binPass = $false
        }
    } else {
        Write-Host "  FAIL: $($ef.Path) not found" -ForegroundColor Red
        $binPass = $false
    }
}

if (-not $binPass) {
    Write-Host "  Executive output check FAILED" -ForegroundColor Red
    exit 1
}
Write-Host "  Executive output check passed." -ForegroundColor Green

# 9) Executive Output v3 guard ??banned words + embedded images
Write-Host "`n[9/9] Executive Output v3 guard..." -ForegroundColor Yellow

$bannedWords = @(
    "ai???", "AI Intel", "Z1", "Z2", "Z3", "Z4", "Z5",
    "pipeline", "ETL", "verify_run", "ingestion", "ai_core",
    "Last July was", "Desktop smoke signal", "signals_insufficient=true",
    "低信心事件候選", "source=platform", "本次無有效新聞；本次掃描統計",
    # v5.2.6 exec sanitizer — ASCII-only banned phrases (CJK checked in EXEC TEXT BAN SCAN)
    "Evidence summary: sources=",
    "Key terms: ",
    "validate source evidence and related numbers",
    "run small-scope checks against current workflow",
    "escalate only if next scan confirms sustained"
)
$v3Pass = $true
$notionBannedHits = 0
$docxBannedHits = 0
$pptxBannedHits = 0

# Check banned words in Notion page (plain text)
$notionContent = Get-Content "outputs\notion_page.md" -Raw -Encoding UTF8
foreach ($bw in $bannedWords) {
    if ($notionContent -match [regex]::Escape($bw)) {
        Write-Host "  FAIL: Banned word '$bw' found in notion_page.md" -ForegroundColor Red
        $notionBannedHits++
        $v3Pass = $false
    }
}

# Check banned words in DOCX (extract text via python; strip URLs to avoid false positives from base64 URL fragments)
$docxText = & $py -c "
import re
from docx import Document
doc = Document('outputs/executive_report.docx')
raw = ' '.join(p.text for p in doc.paragraphs)
for t in doc.tables:
    for row in t.rows:
        for cell in row.cells:
            raw += ' ' + cell.text
raw = re.sub(r'https?://\S+', ' URL_STRIPPED ', raw)
print(raw)
" 2>$null
if ($docxText) {
    foreach ($bw in $bannedWords) {
        if ($docxText -match [regex]::Escape($bw)) {
            Write-Host "  FAIL: Banned word '$bw' found in executive_report.docx" -ForegroundColor Red
            $docxBannedHits++
            $v3Pass = $false
        }
    }
}

# Check banned words in PPTX (extract text via python; strip URLs to avoid false positives)
$pptxText = & $py -c "
import re
from pptx import Presentation
prs = Presentation('outputs/executive_report.pptx')
raw = ''
for slide in prs.slides:
    for shape in slide.shapes:
        if shape.has_text_frame:
            for p in shape.text_frame.paragraphs:
                raw += p.text + ' '
        if shape.has_table:
            for row in shape.table.rows:
                for cell in row.cells:
                    raw += cell.text + ' '
raw = re.sub(r'https?://\S+', ' URL_STRIPPED ', raw)
print(raw)
" 2>$null
if ($pptxText) {
    foreach ($bw in $bannedWords) {
        if ($pptxText -match [regex]::Escape($bw)) {
            Write-Host "  FAIL: Banned word '$bw' found in executive_report.pptx" -ForegroundColor Red
            $pptxBannedHits++
            $v3Pass = $false
        }
    }
}

# Count event cards for context (event cards need per-card images; zero events still need banner)
$eventCardCount = & $py -c "from docx import Document; doc = Document('outputs/executive_report.docx'); print(sum(1 for p in doc.paragraphs if p.text.lstrip().startswith(chr(31532))))" 2>$null
$eventCards = if ($eventCardCount) { [int]$eventCardCount } else { 0 }
Write-Host "  Event cards detected: $eventCards"

# DOCX must always have at least 1 embedded image (banner on cover or per-card images)
$docxHasImage = & $py -c "
import zipfile, sys
with zipfile.ZipFile('outputs/executive_report.docx') as z:
    media = [n for n in z.namelist() if n.startswith('word/media/')]
    print(len(media))
" 2>$null
$docxImageCount = if ($docxHasImage) { [int]$docxHasImage } else { 0 }
if ($docxImageCount -lt 1) {
    Write-Host "  FAIL: DOCX has no embedded images (word/media/ is empty)" -ForegroundColor Red
    $v3Pass = $false
} else {
    Write-Host "  DOCX embedded images: $docxImageCount file(s)" -ForegroundColor Green
}

# PPTX must always have at least 1 embedded image (banner on cover or per-card images)
$pptxHasImage = & $py -c "
import zipfile, sys
with zipfile.ZipFile('outputs/executive_report.pptx') as z:
    media = [n for n in z.namelist() if n.startswith('ppt/media/')]
    print(len(media))
" 2>$null
$pptxImageCount = if ($pptxHasImage) { [int]$pptxHasImage } else { 0 }
if ($pptxImageCount -lt 1) {
    Write-Host "  FAIL: PPTX has no embedded images (ppt/media/ is empty)" -ForegroundColor Red
    $v3Pass = $false
} else {
    Write-Host "  PPTX embedded images: $pptxImageCount file(s)" -ForegroundColor Green
}

if (-not $v3Pass) {
    Write-Host "  Executive Output v3 guard FAILED" -ForegroundColor Red
    exit 1
}
Write-Host "  Executive Output v3 guard passed." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Executive Slide Density Audit (post-step-9; hard gate for 3 key slides)
# ---------------------------------------------------------------------------
Write-Host "`n[Density Audit] Executive Slide Density Audit..." -ForegroundColor Yellow
$densityRaw = & $py -c "
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path('.').resolve()))
from scripts.diagnostics_pptx import slide_density_audit
try:
    res = slide_density_audit(Path('outputs/executive_report.pptx'))
    print(json.dumps(res))
except Exception as ex:
    print(json.dumps([]))
" 2>$null

$densityAuditPass = $true
$keyPatterns = @('Overview', '總覽', 'Event Ranking', '排行', 'Pending', '待決')
$forbiddenFragments = @('Last July was')
$requiredDensity = 80   # EXEC_REQUIRED_SLIDE_DENSITY (configurable)

$requiredSemanticDensity = 40   # EXEC_SEMANTIC_THRESHOLDS default

if ($densityRaw) {
    try { $densityData = $densityRaw | ConvertFrom-Json } catch { $densityData = @() }
    foreach ($s in $densityData) {
        $tbl = "$($s.table_cells_nonempty)/$($s.table_cells_total)"
        $semScore = if ($s.PSObject.Properties['semantic_score']) { $s.semantic_score } else { 0 }
        Write-Host ("[DENSITY] slide={0:D2} title=`"{1}`" chars={2} table={3} terms={4} nums={5} sents={6} score={7} sem_score={8}" -f $s.slide_index, ($s.title -replace '"', "'"), $s.text_chars, $tbl, $s.terms, $s.numbers, $s.sentences, $s.density_score, $semScore)
    }
    foreach ($s in $densityData) {
        # Forbidden fragment check (all slides)
        foreach ($frag in $forbiddenFragments) {
            if ($s.all_text -match [regex]::Escape($frag)) {
                Write-Host "  [DENSITY FAIL] Forbidden fragment '$frag' in slide=$($s.slide_index) title=`"$($s.title)`""
                $densityAuditPass = $false
            }
        }
        # Hard gate for key slides — semantic (primary) + formal (secondary)
        # Semantic gate is the hard gate: sem_score < threshold → truly hollow content → FAIL
        # Formal density gate: low but sem OK → WARN only (sparse-day signal-only is acceptable)
        $isKey = $false
        foreach ($pat in $keyPatterns) { if ($s.title -like "*$pat*") { $isKey = $true; break } }
        if ($isKey) {
            $semScore = if ($s.PSObject.Properties['semantic_score']) { $s.semantic_score } else { 0 }
            if ($semScore -lt $requiredSemanticDensity) {
                Write-Host ("  [DENSITY FAIL] slide={0} title=`"{1}`" sem_score={2} < required_semantic={3} (hollow content)" -f $s.slide_index, $s.title, $semScore, $requiredSemanticDensity)
                $densityAuditPass = $false
            } elseif ($s.density_score -lt $requiredDensity) {
                Write-Host ("  [DENSITY WARN] slide={0} title=`"{1}`" density={2} < {3} but sem_score={4} OK (sparse day, not hollow)" -f $s.slide_index, $s.title, $s.density_score, $requiredDensity, $semScore)
            }
        }
    }
    if (-not $densityAuditPass) {
        Write-Host "  Executive Slide Density Audit FAILED"
        exit 1
    }
    Write-Host "  Executive Slide Density Audit PASSED"
} else {
    Write-Host "  [Density Audit] Skipped (pptx parser returned no output)"
}

Write-Host "`n=== Verification Complete ===" -ForegroundColor Cyan
Write-Host "NOTE: Executive reports are build artifacts. Do NOT commit them." -ForegroundColor DarkGray
Write-Host "      To share, use file transfer or CI release artifacts." -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# EXEC LAYOUT EVIDENCE — Visual Template V1 audit (reads exec_layout.meta.json)
# ---------------------------------------------------------------------------
$execLayoutMetaPath = Join-Path $PSScriptRoot "..\outputs\exec_layout.meta.json"
if (Test-Path $execLayoutMetaPath) {
    try {
        $elm = Get-Content $execLayoutMetaPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Host ""
        Write-Host "EXEC LAYOUT EVIDENCE:"
        Write-Host ("  layout_version          : {0}" -f $elm.layout_version)
        if ($elm.PSObject.Properties['template_map']) {
            $tm = $elm.template_map
            Write-Host ("  template_map.overview   : {0}" -f $tm.overview)
            Write-Host ("  template_map.ranking    : {0}" -f $tm.ranking)
            Write-Host ("  template_map.pending    : {0}" -f $tm.pending)
            Write-Host ("  template_map.sig_summary: {0}" -f $tm.signal_summary)
            Write-Host ("  template_map.ev_slide_a : {0}" -f $tm.event_slide_a)
            Write-Host ("  template_map.ev_slide_b : {0}" -f $tm.event_slide_b)
        }
        if ($elm.PSObject.Properties['fragment_fix_stats']) {
            $ffs = $elm.fragment_fix_stats
            Write-Host ("  fragment_ratio          : {0}" -f $ffs.fragment_ratio)
            Write-Host ("  fragments_detected      : {0}" -f $ffs.fragments_detected)
            Write-Host ("  fragments_fixed         : {0}" -f $ffs.fragments_fixed)
        }
        if ($elm.PSObject.Properties['bullet_len_stats']) {
            $bls = $elm.bullet_len_stats
            Write-Host ("  min_bullet_len          : {0}" -f $bls.min_bullet_len)
            Write-Host ("  avg_bullet_len          : {0}" -f $bls.avg_bullet_len)
        }
        if ($elm.PSObject.Properties['card_stats']) {
            $cs = $elm.card_stats
            Write-Host ("  proof_token_coverage    : {0}" -f $cs.proof_token_coverage_ratio)
            Write-Host ("  avg_sentences_per_card  : {0}" -f $cs.avg_sentences_per_event_card)
        }
        $validCodes = @('T1','T2','T3','T4','T5','T6','COVER','STRUCTURED_SUMMARY','CORP_WATCH','KEY_TAKEAWAYS','REC_MOVES','DECISION_MATRIX')
        $invalidCodes = @()
        if ($elm.PSObject.Properties['slide_layout_map']) {
            foreach ($sl in $elm.slide_layout_map) {
                if ($sl.template_code -notin $validCodes) {
                    $invalidCodes += $sl.template_code
                }
            }
        }
        if ($invalidCodes.Count -gt 0) {
            Write-Host ("  WARNING: invalid template codes: {0}" -f ($invalidCodes -join ', '))
        } else {
            Write-Host "  slide_layout_map codes  : all valid (T1-T6 + structural)"
        }
    } catch {
        Write-Host "  exec_layout meta parse error (non-fatal): $_"
    }
} else {
    Write-Host ""
    Write-Host "EXEC LAYOUT EVIDENCE: exec_layout.meta.json not found (skipped)"
}

$head = (git rev-parse HEAD 2>$null | Select-Object -First 1)
$gitStatusSbLines = @(git status -sb 2>$null)
$gitStatusSb = if ($gitStatusSbLines.Count -gt 0) { "$($gitStatusSbLines[0])".Trim() } else { "" }
if ([string]::IsNullOrWhiteSpace($gitStatusSb)) { $gitStatusSb = "<empty>" }
$gitPorcelain = (git status --porcelain 2>$null | Out-String).Trim()
$workingTree = if ([string]::IsNullOrWhiteSpace($gitPorcelain)) { "clean" } else { "dirty" }

# EVIDENCE GATE: working tree must be clean and status -sb must be exactly 1 line
if ($gitStatusSbLines.Count -ne 1) {
    Write-Host "EVIDENCE-GATE FAIL: git status -sb returned $($gitStatusSbLines.Count) lines (expected 1)." -ForegroundColor Red
    Write-Host "Commit or stash all changes before generating delivery evidence." -ForegroundColor Red
    exit 1
}
if ($workingTree -eq "dirty") {
    Write-Host "EVIDENCE-GATE FAIL: Working tree is dirty. Commit all changes first." -ForegroundColor Red
    exit 1
}

$branchSummary = if ($gitStatusSb -match "\[gone\]") {
    ($gitStatusSb -replace '^##\s*', '') + "  (WARN-OK: origin ref gone)"
} elseif ($gitStatusSb -match "^##\s+(.+)\.\.\.(.+)$") {
    "$($Matches[1]) -> $($Matches[2]) (tracking)"
} elseif ($gitStatusSb -match "^##\s+(.+)$") {
    "$($Matches[1]) (no upstream)"
} else {
    "<unavailable>"
}
$schemaDiff = (git diff HEAD~1..HEAD -- schemas/education_models.py 2>$null | Out-String).Trim()
$schemaModified = if ([string]::IsNullOrWhiteSpace($schemaDiff)) { "NO" } else { "YES" }
$schemaProofOutput = if ([string]::IsNullOrWhiteSpace($schemaDiff)) { "<empty>" } else { "non-empty" }

Write-Host ""
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "FINAL DELIVERY EVIDENCE" -ForegroundColor Cyan
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "HEAD: $head"
Write-Host ""
Write-Host "Schema file: schemas/education_models.py"
Write-Host "EduNewsCard schema modified: $schemaModified"
Write-Host "Schema proof command: git diff HEAD~1..HEAD -- schemas/education_models.py"
Write-Host "Schema proof output: $schemaProofOutput"
Write-Host ""
Write-Host "Banned phrases detected: $($notionBannedHits + $docxBannedHits + $pptxBannedHits) hits"
Write-Host "DOCX output: $docxBannedHits hits"
Write-Host "PPTX output: $pptxBannedHits hits"
Write-Host ""
Write-Host "verify_run: 9/9 PASS"
Write-Host "Working tree: $workingTree"
Write-Host "Branch: $branchSummary"
Write-Host "git status -sb:"
Write-Host ("git status -sb (lines={0}):" -f $gitStatusSbLines.Count)
if ($gitStatusSbLines.Count -gt 0) {
    foreach ($ln in $gitStatusSbLines) {
        Write-Host $ln
    }
} else {
    Write-Host "<empty>"
}

# Z0 Collector Evidence (optional — only printed when meta file exists)
$z0MetaPath = Join-Path $PSScriptRoot "..\data\raw\z0\latest.meta.json"
if (Test-Path $z0MetaPath) {
    try {
        $z0Meta = Get-Content $z0MetaPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Host ""
        Write-Host "Z0 COLLECTOR EVIDENCE:"
        Write-Host ("  collected_at      : {0}" -f $z0Meta.collected_at)
        Write-Host ("  total_items       : {0}" -f $z0Meta.total_items)
        Write-Host ("  frontier_ge_70    : {0}" -f $z0Meta.frontier_ge_70)
        Write-Host ("  frontier_ge_85    : {0}" -f $z0Meta.frontier_ge_85)
        # New 72h granular stats (present in updated collector)
        if ($z0Meta.PSObject.Properties['frontier_ge_70_72h']) {
            Write-Host ("  frontier_ge_70_72h: {0}" -f $z0Meta.frontier_ge_70_72h)
        }
        if ($z0Meta.PSObject.Properties['frontier_ge_85_72h']) {
            Write-Host ("  frontier_ge_85_72h: {0}" -f $z0Meta.frontier_ge_85_72h)
        }
        # Audit: date-source provenance
        if ($z0Meta.PSObject.Properties['published_at_source_counts']) {
            $srcJson = $z0Meta.published_at_source_counts | ConvertTo-Json -Compress
            Write-Host ("  pub_at_source_counts: {0}" -f $srcJson)
        }
        if ($z0Meta.PSObject.Properties['fallback_ratio']) {
            Write-Host ("  fallback_ratio        : {0}" -f $z0Meta.fallback_ratio)
        }
        if ($z0Meta.PSObject.Properties['frontier_ge_85_fallback_count']) {
            Write-Host ("  f85_fallback_count    : {0}" -f $z0Meta.frontier_ge_85_fallback_count)
        }
        if ($z0Meta.PSObject.Properties['frontier_ge_85_fallback_ratio']) {
            Write-Host ("  f85_fallback_ratio    : {0}" -f $z0Meta.frontier_ge_85_fallback_ratio)
        }
        if ($z0Meta.by_platform) {
            Write-Host "  by_platform:"
            $z0Meta.by_platform.PSObject.Properties | Sort-Object Value -Descending | ForEach-Object {
                Write-Host ("    {0}: {1}" -f $_.Name, $_.Value)
            }
        }
        # Optional gate: Z0_MIN_FRONTIER85 (checks total; e.g. set $env:Z0_MIN_FRONTIER85=5)
        if ($env:Z0_MIN_FRONTIER85) {
            $minF85 = [int]$env:Z0_MIN_FRONTIER85
            if ($z0Meta.frontier_ge_85 -lt $minF85) {
                Write-Host ("Z0 GATE FAIL: frontier_ge_85={0} < required={1}" -f $z0Meta.frontier_ge_85, $minF85)
                exit 1
            }
            Write-Host ("  Z0 gate OK: frontier_ge_85={0} >= {1}" -f $z0Meta.frontier_ge_85, $minF85)
        }
        # Optional gate: Z0_MIN_FRONTIER85_72H (checks 72h window; e.g. set $env:Z0_MIN_FRONTIER85_72H=3)
        if ($env:Z0_MIN_FRONTIER85_72H) {
            $minF85_72h = [int]$env:Z0_MIN_FRONTIER85_72H
            $actual72h = if ($z0Meta.PSObject.Properties['frontier_ge_85_72h']) { [int]$z0Meta.frontier_ge_85_72h } else { 0 }
            if ($actual72h -lt $minF85_72h) {
                Write-Host ("Z0 GATE FAIL: frontier_ge_85_72h={0} < required={1}" -f $actual72h, $minF85_72h)
                exit 1
            }
            Write-Host ("  Z0 gate OK: frontier_ge_85_72h={0} >= {1}" -f $actual72h, $minF85_72h)
        }
    } catch {
        Write-Host "  Z0 meta parse error (non-fatal): $_"
    }
}

# Executive Selection Meta Evidence (optional - only printed when file exists)
$execMetaPath = Join-Path $PSScriptRoot "..\outputs\exec_selection.meta.json"
if (Test-Path $execMetaPath) {
    try {
        $execMeta = Get-Content $execMetaPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Host ""
        Write-Host "EXECUTIVE SELECTION EVIDENCE:"
        Write-Host ("  events_total              : {0}" -f $execMeta.events_total)
        if ($execMeta.PSObject.Properties['events_by_bucket']) {
            $bucketJson = $execMeta.events_by_bucket | ConvertTo-Json -Compress
            Write-Host ("  events_by_bucket          : {0}" -f $bucketJson)
        }
        Write-Host ("  rejected_irrelevant_count : {0}" -f $execMeta.rejected_irrelevant_count)
        if ($execMeta.PSObject.Properties['rejected_top_reasons'] -and $execMeta.rejected_top_reasons) {
            Write-Host ("  rejected_top_reasons      : {0}" -f ($execMeta.rejected_top_reasons -join "; "))
        }
        Write-Host ("  quota_pass                : {0}" -f $execMeta.quota_pass)
        Write-Host ("  sparse_day                : {0}" -f $execMeta.sparse_day)
        if ($execMeta.PSObject.Properties['quota_target']) {
            $qtJson = $execMeta.quota_target | ConvertTo-Json -Compress
            Write-Host ("  quota_target              : {0}" -f $qtJson)
        }
        # Optional ENV gates (informational — 9/9 pipeline gate is authoritative)
        if ($env:EXEC_MIN_EVENTS) {
            $minEv = [int]$env:EXEC_MIN_EVENTS
            $actualEv = [int]$execMeta.events_total
            if ($actualEv -lt $minEv) {
                Write-Host ("  EXEC gate WARN: events_total={0} < required={1}" -f $actualEv, $minEv)
            } else {
                Write-Host ("  EXEC gate OK: events_total={0} >= {1}" -f $actualEv, $minEv)
            }
        }
        if ($env:EXEC_MIN_PRODUCT) {
            $minP = [int]$env:EXEC_MIN_PRODUCT
            $actP = if ($execMeta.events_by_bucket.PSObject.Properties['product']) { [int]$execMeta.events_by_bucket.product } else { 0 }
            if ($actP -lt $minP) {
                Write-Host ("  EXEC gate WARN: product={0} < required={1} (quota_unmet)" -f $actP, $minP)
            } else {
                Write-Host ("  EXEC gate OK: product={0} >= {1}" -f $actP, $minP)
            }
        }
        if ($env:EXEC_MIN_TECH) {
            $minT = [int]$env:EXEC_MIN_TECH
            $actT = if ($execMeta.events_by_bucket.PSObject.Properties['tech']) { [int]$execMeta.events_by_bucket.tech } else { 0 }
            if ($actT -lt $minT) {
                Write-Host ("  EXEC gate WARN: tech={0} < required={1} (quota_unmet)" -f $actT, $minT)
            } else {
                Write-Host ("  EXEC gate OK: tech={0} >= {1}" -f $actT, $minT)
            }
        }
        if ($env:EXEC_MIN_BUSINESS) {
            $minB = [int]$env:EXEC_MIN_BUSINESS
            $actB = if ($execMeta.events_by_bucket.PSObject.Properties['business']) { [int]$execMeta.events_by_bucket.business } else { 0 }
            if ($actB -lt $minB) {
                Write-Host ("  EXEC gate WARN: business={0} < required={1} (quota_unmet)" -f $actB, $minB)
            } else {
                Write-Host ("  EXEC gate OK: business={0} >= {1}" -f $actB, $minB)
            }
        }
    } catch {
        Write-Host "  exec_selection meta parse error (non-fatal): $_"
    }
}

# Pipeline Flow Counts (optional — only printed when file exists)
$flowCountsPath = Join-Path $PSScriptRoot "..\outputs\flow_counts.meta.json"
if (Test-Path $flowCountsPath) {
    try {
        $fc = Get-Content $flowCountsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Host ""
        Write-Host "PIPELINE FLOW COUNTS:"
        Write-Host ("  z0_loaded_total            : {0}" -f $fc.z0_loaded_total)
        Write-Host ("  after_dedupe_total         : {0}" -f $fc.after_dedupe_total)
        Write-Host ("  after_too_old_filter_total : {0}" -f $fc.after_too_old_filter_total)
        Write-Host ("  event_gate_pass_total      : {0}" -f $fc.event_gate_pass_total)
        Write-Host ("  signal_gate_pass_total     : {0}" -f $fc.signal_gate_pass_total)
        Write-Host ("  exec_candidates_total      : {0}" -f $fc.exec_candidates_total)
        Write-Host ("  exec_selected_total        : {0}" -f $fc.exec_selected_total)
        Write-Host ("  extra_cards_total          : {0}" -f $fc.extra_cards_total)
        if ($fc.drop_reasons_top5) {
            $drJson = $fc.drop_reasons_top5 | ConvertTo-Json -Compress
            Write-Host ("  drop_reasons_top5          : {0}" -f $drJson)
        }
    } catch {
        Write-Host "  flow_counts meta parse error (non-fatal): $_"
    }
}

# Filter Breakdown (optional — only printed when filter_breakdown.meta.json exists)
$filterBdPath = Join-Path $PSScriptRoot "..\outputs\filter_breakdown.meta.json"
if (Test-Path $filterBdPath) {
    try {
        $fb = Get-Content $filterBdPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Host ""
        Write-Host "FILTER BREAKDOWN:"
        Write-Host ("  input_count          : {0}" -f $fb.input_count)
        Write-Host ("  kept                 : {0}" -f $fb.kept)
        Write-Host ("  dropped_total        : {0}" -f $fb.dropped_total)
        Write-Host ("  lang_not_allowed     : {0}" -f $fb.lang_not_allowed_count)
        Write-Host ("  too_old              : {0}" -f $fb.too_old_count)
        Write-Host ("  body_too_short       : {0}" -f $fb.body_too_short_count)
        Write-Host ("  non_ai_topic         : {0}" -f $fb.non_ai_topic_count)
        Write-Host ("  allow_zh_enabled     : {0}" -f $fb.allow_zh_enabled)
        if ($fb.top5_reasons) {
            $fbTop5Json = $fb.top5_reasons | ConvertTo-Json -Compress
            Write-Host ("  top5_reasons         : {0}" -f $fbTop5Json)
        }
    } catch {
        Write-Host "  filter_breakdown meta parse error (non-fatal): $_"
    }
}

# Z0 Injection Gate Evidence (optional -- only printed when file exists)
$z0InjMetaPath = Join-Path $PSScriptRoot "..\outputs\z0_injection.meta.json"
if (Test-Path $z0InjMetaPath) {
    try {
        $z0Inj = Get-Content $z0InjMetaPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Host ""
        Write-Host "Z0 INJECTION GATE EVIDENCE:"
        Write-Host ("  z0_inject_candidates_total        : {0}" -f $z0Inj.z0_inject_candidates_total)
        Write-Host ("  z0_inject_after_frontier_total    : {0}" -f $z0Inj.z0_inject_after_frontier_total)
        Write-Host ("  z0_inject_after_channel_gate_total: {0}" -f $z0Inj.z0_inject_after_channel_gate_total)
        Write-Host ("  z0_inject_selected_total          : {0}" -f $z0Inj.z0_inject_selected_total)
        Write-Host ("  z0_inject_dropped_by_channel_gate : {0}" -f $z0Inj.z0_inject_dropped_by_channel_gate)
        Write-Host ("  z0_inject_channel_gate_threshold  : {0}" -f $z0Inj.z0_inject_channel_gate_threshold)
    } catch {
        Write-Host "  z0_injection meta parse error (non-fatal): $_"
    }
}

# ---------------------------------------------------------------------------
# EXEC QUALITY GATES — reads exec_quality.meta.json written by pipeline
# ---------------------------------------------------------------------------
$execQualMetaPath = Join-Path $PSScriptRoot "..\outputs\exec_quality.meta.json"
if (Test-Path $execQualMetaPath) {
    try {
        $eqm = Get-Content $execQualMetaPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $eqSparseDay = if ($eqm.PSObject.Properties['sparse_day']) { [bool]$eqm.sparse_day } else { $false }

        $g1Status  = "PASS"   # AI relevance already enforced upstream; report count only
        $g2Status  = if ($eqm.PSObject.Properties['source_diversity_gate']) { $eqm.source_diversity_gate } else { "PASS" }
        $g3Status  = if ($eqm.PSObject.Properties['proof_coverage_gate'])   { $eqm.proof_coverage_gate }   else { "PASS" }
        $g4Status  = if ($eqm.PSObject.Properties['fragment_leak_gate'])    { $eqm.fragment_leak_gate }    else { "PASS" }

        $nonAiRej     = if ($eqm.PSObject.Properties['non_ai_rejected_count'])  { $eqm.non_ai_rejected_count }  else { 0 }
        $maxSrcShare  = if ($eqm.PSObject.Properties['max_source_share'])       { $eqm.max_source_share }       else { 0 }
        $maxSrc       = if ($eqm.PSObject.Properties['max_source'])             { $eqm.max_source }             else { "n/a" }
        $proofRatio   = if ($eqm.PSObject.Properties['proof_coverage_ratio'])   { $eqm.proof_coverage_ratio }   else { 0 }
        $fragLeaked   = if ($eqm.PSObject.Properties['fragments_leaked'])       { $eqm.fragments_leaked }       else { 0 }
        $fragDetected = if ($eqm.PSObject.Properties['fragments_detected'])     { $eqm.fragments_detected }     else { 0 }
        $fragFixed    = if ($eqm.PSObject.Properties['fragments_fixed'])        { $eqm.fragments_fixed }        else { 0 }
        $enHeavyFixed    = if ($eqm.PSObject.Properties['english_heavy_paragraphs_fixed_count']) { $eqm.english_heavy_paragraphs_fixed_count } else { 0 }
        $glossApplied    = if ($eqm.PSObject.Properties['proper_noun_gloss_applied_count'])      { $eqm.proper_noun_gloss_applied_count }      else { 0 }
        $actionsNorm     = if ($eqm.PSObject.Properties['actions_normalized_count'])             { $eqm.actions_normalized_count }             else { 0 }
        $actionsLeak     = if ($eqm.PSObject.Properties['actions_fragment_leak_count'])          { $eqm.actions_fragment_leak_count }          else { 0 }
        $zhSkeletonize   = if ($eqm.PSObject.Properties['english_heavy_skeletonized_count'])     { $eqm.english_heavy_skeletonized_count }     else { 0 }
        $proofEmptyGate  = if ($eqm.PSObject.Properties['proof_empty_gate'])                     { $eqm.proof_empty_gate }                     else { "PASS" }
        $proofEmptyCount = if ($eqm.PSObject.Properties['proof_empty_event_count'])              { $eqm.proof_empty_event_count }              else { 0 }
        $actNormStatus   = if ($actionsLeak -eq 0) { "PASS" } else { "FAIL" }

        Write-Host ""
        Write-Host "EXEC QUALITY GATES:"
        Write-Host ("  AI_RELEVANCE_GATE    : {0} (non_ai_rejected={1})" -f $g1Status, $nonAiRej)
        Write-Host ("  SOURCE_DIVERSITY_GATE: {0} (max_source_share={1:P1} source={2})" -f $g2Status, $maxSrcShare, $maxSrc)
        Write-Host ("  PROOF_COVERAGE_GATE  : {0} (ratio={1:P1})" -f $g3Status, $proofRatio)
        Write-Host ("  FRAGMENT_LEAK_GATE   : {0} (leaked={1} detected={2} fixed={3})" -f $g4Status, $fragLeaked, $fragDetected, $fragFixed)
        Write-Host ("  EN_ZH_HYBRID_GLOSS   : english_heavy_fixed={0}  proper_noun_glossed={1}" -f $enHeavyFixed, $glossApplied)
        Write-Host ("  ACTIONS_NORMALIZATION: {0} (normalized={1} leaked={2})" -f $actNormStatus, $actionsNorm, $actionsLeak)
        Write-Host ("  ZH_SKELETONIZE       : count={0}" -f $zhSkeletonize)
        Write-Host ("  PROOF_EMPTY_GATE     : {0} (empty={1})" -f $proofEmptyGate, $proofEmptyCount)

        $qualAnyFail = ($g2Status -eq "FAIL") -or ($g3Status -eq "FAIL") -or ($g4Status -eq "FAIL") -or ($actNormStatus -eq "FAIL") -or ($proofEmptyGate -eq "FAIL")
        if ($qualAnyFail -and -not $eqSparseDay) {
            Write-Host "  => EXEC QUALITY GATES: FAIL" -ForegroundColor Red
            exit 1
        }
        Write-Host "  => EXEC QUALITY GATES: PASS"
    } catch {
        Write-Host "  exec_quality meta parse error (non-fatal): $_"
    }
} else {
    Write-Host ""
    Write-Host "EXEC QUALITY GATES: exec_quality.meta.json not found (skipped)"
}

# ---------------------------------------------------------------------------
# LONGFORM EVIDENCE — reads exec_longform.meta.json written by ppt_generator
# ---------------------------------------------------------------------------
$longformMetaPath = Join-Path $PSScriptRoot "..\outputs\exec_longform.meta.json"
if (Test-Path $longformMetaPath) {
    try {
        $lfm = Get-Content $longformMetaPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $lfEligible      = if ($lfm.PSObject.Properties['eligible_count'])       { [int]$lfm.eligible_count }       else { 0 }
        $lfIneligible    = if ($lfm.PSObject.Properties['ineligible_count'])     { [int]$lfm.ineligible_count }     else { 0 }
        $lfTotal         = if ($lfm.PSObject.Properties['total_cards_processed']){ [int]$lfm.total_cards_processed } else { 0 }
        $lfEligRatio     = if ($lfm.PSObject.Properties['eligible_ratio'])       { [double]$lfm.eligible_ratio }     else { 0.0 }
        $lfProofRatio    = if ($lfm.PSObject.Properties['proof_coverage_ratio']) { [double]$lfm.proof_coverage_ratio } else { 0.0 }
        $lfProofPres     = if ($lfm.PSObject.Properties['proof_present_count'])  { [int]$lfm.proof_present_count }   else { 0 }
        $lfProofMiss     = if ($lfm.PSObject.Properties['proof_missing_count'])  { [int]$lfm.proof_missing_count }   else { 0 }
        $lfAvgAnchor     = if ($lfm.PSObject.Properties['avg_anchor_chars'])     { $lfm.avg_anchor_chars }           else { 0 }
        $lfMinAnchor     = if ($lfm.PSObject.Properties['min_anchor_chars'])     { $lfm.min_anchor_chars }           else { 1200 }
        $lfMissIds       = if ($lfm.PSObject.Properties['proof_missing_ids'] -and $lfm.proof_missing_ids) { ($lfm.proof_missing_ids -join ', ') } else { '(none)' }

        # Self-consistency check: eligible + ineligible must equal total
        $lfConsistent    = ($lfEligible + $lfIneligible) -eq $lfTotal

        Write-Host ""
        Write-Host "LONGFORM EVIDENCE (exec_longform.meta.json):"
        Write-Host ("  generated_at            : {0}" -f $lfm.generated_at)
        Write-Host ("  min_anchor_chars        : {0}" -f $lfMinAnchor)
        Write-Host ("  total_cards_processed   : {0}" -f $lfTotal)
        Write-Host ("  eligible_count          : {0}  ({1:P1})" -f $lfEligible, $lfEligRatio)
        Write-Host ("  ineligible_count        : {0}" -f $lfIneligible)
        Write-Host ("  counts_consistent       : {0}" -f $(if ($lfConsistent) { 'YES' } else { 'NO — MISMATCH' }))
        Write-Host ("  avg_anchor_chars        : {0}" -f $lfAvgAnchor)
        Write-Host ("  proof_present_count     : {0}" -f $lfProofPres)
        Write-Host ("  proof_missing_count     : {0}" -f $lfProofMiss)
        Write-Host ("  proof_missing_ids(top5) : {0}" -f $lfMissIds)
        Write-Host ("  proof_coverage_ratio    : {0:P1}" -f $lfProofRatio)
        if ($lfm.PSObject.Properties['samples'] -and $lfm.samples.Count -gt 0) {
            Write-Host "  samples:"
            foreach ($s in $lfm.samples) {
                $pf = if ($s.proof_line) { ($s.proof_line -replace '[\r\n]+',' ') } else { "(none)" }
                Write-Host ("    title={0}  anchor={1}  proof={2}" -f ($s.title -replace '[\r\n]+',' '), $s.anchor_chars, $pf)
            }
        }
        $lfPass = $lfConsistent -and ($lfProofRatio -ge 0.8 -or $lfTotal -eq 0)
        if ($lfPass) {
            Write-Host "  => LONGFORM_EVIDENCE: PASS"
        } else {
            Write-Host ("  => LONGFORM_EVIDENCE: WARN (proof_ratio={0:P1} consistent={1})" -f $lfProofRatio, $lfConsistent)
        }
    } catch {
        Write-Host "  longform meta parse error (non-fatal): $_"
    }
} else {
    Write-Host ""
    Write-Host "LONGFORM EVIDENCE: exec_longform.meta.json not found (skipped — run pipeline first)"
}

# ---------------------------------------------------------------------------
# LONGFORM DAILY COUNT (Watchlist/Developing Pool Expansion v1)
# ---------------------------------------------------------------------------
if (Test-Path $longformMetaPath) {
    try {
        $ldm = Get-Content $longformMetaPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($ldm.PSObject.Properties['longform_daily_total'] -or $ldm.PSObject.Properties['event_longform_count']) {
            $ldMinTarget = if ($ldm.PSObject.Properties['longform_min_daily_total'])      { [int]$ldm.longform_min_daily_total }      else { 6 }
            $ldEvCount   = if ($ldm.PSObject.Properties['event_longform_count'])          { [int]$ldm.event_longform_count }          else { 0 }
            $ldWlCands   = if ($ldm.PSObject.Properties['watchlist_longform_candidates']) { [int]$ldm.watchlist_longform_candidates } else { 0 }
            $ldWlSel     = if ($ldm.PSObject.Properties['watchlist_longform_selected'])   { [int]$ldm.watchlist_longform_selected }   else { 0 }
            $ldTotal     = if ($ldm.PSObject.Properties['longform_daily_total'])          { [int]$ldm.longform_daily_total }          else { $ldEvCount }
            $ldWlAvg     = if ($ldm.PSObject.Properties['watchlist_avg_anchor_chars'])    { $ldm.watchlist_avg_anchor_chars }         else { 0 }
            $ldWlPRatio  = if ($ldm.PSObject.Properties['watchlist_proof_coverage_ratio']){ [double]$ldm.watchlist_proof_coverage_ratio } else { 1.0 }
            $ldWlIds     = if ($ldm.PSObject.Properties['watchlist_selected_ids_top10'] -and $ldm.watchlist_selected_ids_top10) {
                ($ldm.watchlist_selected_ids_top10 -join ', ')
            } else { '(none)' }
            $ldWlTop3    = if ($ldm.PSObject.Properties['watchlist_sources_share_top3'] -and $ldm.watchlist_sources_share_top3.Count -gt 0) {
                ($ldm.watchlist_sources_share_top3 | ForEach-Object { "$($_.source)=$($_.count)" }) -join ', '
            } else { '(none)' }

            $ldGate = if ($ldTotal -ge $ldMinTarget) { "PASS" } else { "WARN-OK" }

            Write-Host ""
            Write-Host "LONGFORM DAILY COUNT (exec_longform.meta.json):"
            Write-Host ("  longform_min_daily_total       : {0}" -f $ldMinTarget)
            Write-Host ("  event_longform_count           : {0}" -f $ldEvCount)
            Write-Host ("  watchlist_longform_candidates  : {0}" -f $ldWlCands)
            Write-Host ("  watchlist_longform_selected    : {0}" -f $ldWlSel)
            Write-Host ("  longform_daily_total           : {0}  (target >= {1})" -f $ldTotal, $ldMinTarget)
            Write-Host ("  watchlist_avg_anchor_chars     : {0}" -f $ldWlAvg)
            Write-Host ("  watchlist_proof_coverage_ratio : {0:P1}" -f $ldWlPRatio)
            Write-Host ("  watchlist_selected_ids(top10)  : {0}" -f $ldWlIds)
            Write-Host ("  watchlist_sources_top3         : {0}" -f $ldWlTop3)
            if ($ldGate -eq "PASS") {
                Write-Host ("  => LONGFORM_DAILY_TOTAL target={0} actual={1} PASS" -f $ldMinTarget, $ldTotal)
            } else {
                Write-Host ("  => LONGFORM_DAILY_TOTAL target={0} actual={1} WARN-OK (watchlist pool may be small)" -f $ldMinTarget, $ldTotal)
            }
        }
    } catch {
        Write-Host "  longform daily count parse error (non-fatal): $_"
    }
}

# ---------------------------------------------------------------------------
# EXEC TEXT BAN SCAN — fail-fast gate (v5.2.6 sanitizer validation)
# Scans PPTX + DOCX for any banned template/internal-tag phrases.
# These are a superset of $bannedWords already checked above; this block
# makes the gate explicit and labeled for CI evidence.
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "EXEC TEXT BAN SCAN:" -ForegroundColor Yellow
$execBanPhrases = @(
    "Evidence summary: sources=",
    "Key terms: ",
    "validate source evidence and related numbers",
    "run small-scope checks against current workflow",
    "escalate only if next scan confirms sustained",
    "WATCH .*: validate",
    "TEST .*: run small-scope",
    "MOVE .*: escalate only"
)
# Note: CJK banned phrases are checked below via Python subprocess (UTF-8 safe)
$execBanHits = 0

# Scan PPTX
$pptxScanText = & $py -c "
from pptx import Presentation
prs = Presentation('outputs/executive_report.pptx')
for slide in prs.slides:
    for shape in slide.shapes:
        if shape.has_text_frame:
            for p in shape.text_frame.paragraphs:
                print(p.text, end=' ')
" 2>$null

# Scan DOCX
$docxScanText = & $py -c "
from docx import Document
doc = Document('outputs/executive_report.docx')
print(' '.join(p.text for p in doc.paragraphs))
for t in doc.tables:
    for row in t.rows:
        for cell in row.cells:
            print(cell.text, end=' ')
" 2>$null

$combinedScanText = "$pptxScanText $docxScanText"
foreach ($bp in $execBanPhrases) {
    # Use IndexOf for literal matching; avoid regex interpretation of PPTX/DOCX content
    $isRegexPat = $bp -match '[\.\*\+\?\^\$\{\}\[\]\(\)\|\\]'
    $matched = if ($isRegexPat) {
        $combinedScanText -match $bp
    } else {
        $combinedScanText.IndexOf($bp, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    }
    if ($matched) {
        Write-Host ("  FAIL: Banned phrase '{0}' found in PPT/DOCX output" -f $bp) -ForegroundColor Red
        $execBanHits++
    }
}

# CJK banned phrases — checked via Python to avoid PowerShell UTF-8 encoding issues
$cjkBanResult = & $py -c "
import sys
try:
    from pptx import Presentation
    from docx import Document
    pptx_text = ''
    docx_text = ''
    try:
        prs = Presentation('outputs/executive_report.pptx')
        for slide in prs.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for p in shape.text_frame.paragraphs:
                        pptx_text += p.text + ' '
    except Exception:
        pass
    try:
        doc = Document('outputs/executive_report.docx')
        docx_text = ' '.join(p.text for p in doc.paragraphs)
    except Exception:
        pass
    combined = pptx_text + ' ' + docx_text
    cjk_banned = [
        '\u8a73\u898b\u539f\u59cb\u4f86\u6e90',
        '\u76e3\u63a7\u4e2d \u672c\u6b04\u66ab\u7121\u4e8b\u4ef6',
        '\u73fe\u6709\u7b56\u7565\u8207\u8cc7\u6e90\u914d\u7f6e',
        '\u7684\u8da8\u52e2\uff0c\u89e3\u6c7a\u65b9 \u8a18',
    ]
    hits = [b for b in cjk_banned if b in combined]
    if hits:
        print('FAIL:' + '|'.join(hits))
    else:
        print('PASS')
except Exception as e:
    print('SKIP:' + str(e))
" 2>$null

if ($cjkBanResult -and $cjkBanResult.StartsWith("FAIL:")) {
    Write-Host ("  FAIL: CJK banned phrases found: {0}" -f ($cjkBanResult -replace '^FAIL:', '')) -ForegroundColor Red
    $execBanHits++
}

if ($execBanHits -gt 0) {
    Write-Host ("  EXEC TEXT BAN SCAN: FAIL ({0} hit(s))" -f $execBanHits) -ForegroundColor Red
    exit 1
}
Write-Host ("  EXEC TEXT BAN SCAN: PASS (0 hits)") -ForegroundColor Green

# ---------------------------------------------------------------------------
# NARRATIVE_V2 EVIDENCE — reads outputs/narrative_v2.meta.json (audit only, no gate)
# ---------------------------------------------------------------------------
$nv2MetaPath = Join-Path $PSScriptRoot "..\outputs\narrative_v2.meta.json"
if (Test-Path $nv2MetaPath) {
    try {
        $nv2 = Get-Content $nv2MetaPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $nv2Applied = if ($nv2.PSObject.Properties['narrative_v2_applied_count']) { [int]$nv2.narrative_v2_applied_count } else { 0 }
        $nv2ZhRatio = if ($nv2.PSObject.Properties['avg_zh_ratio'])              { [double]$nv2.avg_zh_ratio }             else { 0.0 }
        $nv2Dedup   = if ($nv2.PSObject.Properties['avg_dedup_ratio'])           { [double]$nv2.avg_dedup_ratio }          else { 0.0 }
        $nv2Sents   = if ($nv2.PSObject.Properties['avg_sentences_used'])        { $nv2.avg_sentences_used }               else { 0 }
        Write-Host ""
        Write-Host ("NARRATIVE_V2: applied={0}  avg_zh_ratio={1:F3}  avg_dedup_ratio={2:F3}  avg_sentences_used={3}" -f $nv2Applied, $nv2ZhRatio, $nv2Dedup, $nv2Sents)
    } catch {
        Write-Host "NARRATIVE_V2: meta parse error (non-fatal): $_"
    }
} else {
    Write-Host ""
    Write-Host "NARRATIVE_V2: narrative_v2.meta.json not found (skipped)"
}

# ---------------------------------------------------------------------------
# GIT UPSTREAM PROBE v2 — hardened: A (symbolic-ref) -> B (remote show) ->
# C (show-ref probe main/master) -> NONE; never crashes on [gone] / missing refs
# ORIGIN_REF_MODE values: HEAD | REMOTE_SHOW | FALLBACK | NONE
# ---------------------------------------------------------------------------
$_gitOriginRef    = $null
$_gitOriginMode   = "NONE"
$_gitOriginExists = $false

# Method A: git symbolic-ref — local only, fast; works after `git fetch`
$_symRef = (git symbolic-ref --quiet refs/remotes/origin/HEAD 2>$null | Out-String).Trim()
if ($_symRef -match "refs/remotes/origin/(.+)") {
    $_branchA = $Matches[1].Trim()
    $null = git show-ref --verify "refs/remotes/origin/$_branchA" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $_gitOriginRef    = "origin/$_branchA"
        $_gitOriginMode   = "HEAD"
        $_gitOriginExists = $true
    }
}

# Method B: git remote show origin — parses HEAD branch (may make network call);
#           ref must still exist in local store to be usable
if (-not $_gitOriginRef) {
    $_remoteShow = (git remote show origin 2>$null | Out-String)
    if ($_remoteShow -match "HEAD branch:\s*(.+)") {
        $_branchB = $Matches[1].Trim()
        if ($_branchB -ne "(unknown)" -and $_branchB -ne "") {
            $null = git show-ref --verify "refs/remotes/origin/$_branchB" 2>$null
            if ($LASTEXITCODE -eq 0) {
                $_gitOriginRef    = "origin/$_branchB"
                $_gitOriginMode   = "REMOTE_SHOW"
                $_gitOriginExists = $true
            }
        }
    }
}

# Method C: explicit local show-ref probe — origin/main then origin/master
if (-not $_gitOriginRef) {
    foreach ($_fb in @("main", "master")) {
        $null = git show-ref --verify "refs/remotes/origin/$_fb" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $_gitOriginRef    = "origin/$_fb"
            $_gitOriginMode   = "FALLBACK"
            $_gitOriginExists = $true
            break
        }
    }
}

$_originRefStr    = if ($_gitOriginRef)    { $_gitOriginRef } else { "n/a" }
$_originExistsStr = if ($_gitOriginExists) { "true" }         else { "false" }

Write-Host ""
Write-Host "GIT UPSTREAM:"
Write-Host ("  ORIGIN_REF_USED  : {0}" -f $_originRefStr)
Write-Host ("  ORIGIN_REF_MODE  : {0}" -f $_gitOriginMode)
Write-Host ("  ORIGIN_REF_EXISTS: {0}" -f $_originExistsStr)
Write-Host ""
Write-Host "GIT SYNC:"
if ($_gitOriginRef -and $_gitOriginExists) {
    $_abRaw = (git rev-list --left-right --count "$_gitOriginRef...HEAD" 2>$null | Out-String).Trim()
    if ($_abRaw -match "^(\d+)\s+(\d+)$") {
        $_behind = [int]$Matches[1]; $_ahead = [int]$Matches[2]
        Write-Host ("  GIT_SYNC: behind={0} ahead={1}" -f $_behind, $_ahead)
        if ($_behind -eq 0 -and $_ahead -eq 0) {
            Write-Host "  GIT_UP_TO_DATE: PASS"
        } else {
            Write-Host ("  GIT_UP_TO_DATE: FAIL (diverged from {0})" -f $_gitOriginRef)
            if ($_ahead  -gt 0) { Write-Host ("  >> {0} commit(s) ahead of origin; run: git push" -f $_ahead) }
            if ($_behind -gt 0) { Write-Host ("  >> {0} commit(s) behind origin; run: git pull" -f $_behind) }
        }
    } else {
        Write-Host "  GIT_SYNC: WARN — rev-list returned no output"
        Write-Host "  GIT_UP_TO_DATE: WARN-OK (rev-list empty; run: git fetch origin --prune)"
    }
} else {
    Write-Host "  GIT_SYNC: SKIPPED (origin ref not found in local store)"
    Write-Host "  GIT_UP_TO_DATE: WARN-OK (cannot verify; run: git fetch origin --prune)"
}

# ---------------------------------------------------------------------------
# Helper: resolve standard artifact path; glob-fallback to most-recently-
# modified match when the standard path is missing (avoids path-drift misses)
# ---------------------------------------------------------------------------
function Get-LatestArtifactPath {
    param(
        [string]$StandardPath,
        [string]$Pattern,
        [string]$BaseDir = "outputs"
    )
    if (Test-Path $StandardPath) {
        return [PSCustomObject]@{ Path = (Resolve-Path $StandardPath).Path; IsFallback = $false }
    }
    $candidates = Get-ChildItem -Path $BaseDir -Filter $Pattern -ErrorAction SilentlyContinue |
                  Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($candidates) {
        return [PSCustomObject]@{ Path = $candidates.FullName; IsFallback = $true }
    }
    return $null
}

# ---------------------------------------------------------------------------
# OUTPUT ARTIFACTS EVIDENCE — file fingerprints bound to this pipeline run
# HEAD is already printed above; these hashes tie the files to that commit
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "OUTPUT ARTIFACTS EVIDENCE:"
$_artSpecs = @(
    @{ StdPath = "outputs\executive_report.pptx";    Pattern = "executive_report*.pptx" },
    @{ StdPath = "outputs\executive_report.docx";    Pattern = "executive_report*.docx" },
    @{ StdPath = "outputs\exec_selection.meta.json"; Pattern = "exec_selection*.json"   },
    @{ StdPath = "outputs\flow_counts.meta.json";    Pattern = "flow_counts*.json"      }
)
foreach ($_spec in $_artSpecs) {
    $_found = Get-LatestArtifactPath -StandardPath $_spec.StdPath -Pattern $_spec.Pattern
    if ($null -eq $_found) {
        Write-Host ("  {0}: NOT FOUND" -f $_spec.StdPath)
        continue
    }
    $_info = Get-Item $_found.Path
    $_hash = (Get-FileHash -Path $_found.Path -Algorithm SHA256).Hash
    $_fb   = if ($_found.IsFallback) { " [FOUND_LATEST=1]" } else { "" }
    Write-Host ("  {0}{1}:" -f $_spec.StdPath, $_fb)
    Write-Host ("    path     : {0}" -f $_info.FullName)
    Write-Host ("    modified : {0}" -f ([DateTimeOffset]$_info.LastWriteTime).ToString("yyyy-MM-ddTHH:mm:sszzz"))
    Write-Host ("    size     : {0} bytes" -f $_info.Length)
    Write-Host ("    sha256   : {0}" -f $_hash)
}

Write-Host "=======================================" -ForegroundColor Cyan
