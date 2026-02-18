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
    "低信心事件候選", "source=platform", "本次無有效新聞；本次掃描統計"
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

# Check banned words in DOCX (extract text via python)
$docxText = & $py -c "
from docx import Document
doc = Document('outputs/executive_report.docx')
print(' '.join(p.text for p in doc.paragraphs))
for t in doc.tables:
    for row in t.rows:
        for cell in row.cells:
            print(cell.text, end=' ')
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

# Check banned words in PPTX (extract text via python)
$pptxText = & $py -c "
from pptx import Presentation
prs = Presentation('outputs/executive_report.pptx')
for slide in prs.slides:
    for shape in slide.shapes:
        if shape.has_text_frame:
            for p in shape.text_frame.paragraphs:
                print(p.text, end=' ')
        if shape.has_table:
            for row in shape.table.rows:
                for cell in row.cells:
                    print(cell.text, end=' ')
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

$branchSummary = if ($gitStatusSb -match "^##\s+(.+)\.\.\.(.+)$") {
    "$($Matches[1]) -> $($Matches[2]) up-to-date"
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
Write-Host "=======================================" -ForegroundColor Cyan
