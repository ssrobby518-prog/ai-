# open_ppt.ps1 - Non-blocking PPT opener with pointer + time-budgeted scan
# Priority order: P0 pointer file -> P2 deliveries scan -> P1 root executive_report.pptx -> P3 NOT_FOUND
#   P0: latest_delivery.json / latest_delivery.txt (O(1) lookup)
#   P2: scan outputs\deliveries\ (8-second Stopwatch budget; manual 2-layer traversal, PS5.1 compatible)
#   P1: outputs\executive_report.pptx (LAST resort; only when P0 and P2 both fail)
#   P3: NOT_FOUND / exit 2
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\open_ppt.ps1

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$repoRoot = Split-Path $PSScriptRoot -Parent

# ---- Locate outputsRoot (R0 inside repo; R1 in parent) ----
$R0 = Join-Path $repoRoot "outputs"
$R1 = Join-Path (Split-Path $repoRoot -Parent) "outputs"

$outputsRoot = $null
foreach ($candidate in @($R0, $R1)) {
    if (Test-Path $candidate) { $outputsRoot = $candidate; break }
}

if ($null -eq $outputsRoot) {
    Write-Output "OPEN_PPT: NOT_FOUND (outputs root not found)"
    exit 2
}

$pptxPath = $null

# ---- P0: pointer files (O(1) lookup) ----
$jsonPointer = Join-Path $outputsRoot "latest_delivery.json"
$txtPointer  = Join-Path $outputsRoot "latest_delivery.txt"

if ($null -eq $pptxPath -and (Test-Path $jsonPointer)) {
    try {
        $raw   = [System.IO.File]::ReadAllText($jsonPointer, [System.Text.Encoding]::UTF8)
        $match = [regex]::Match($raw, '"pptx_path"\s*:\s*"([^"]+)"')
        if ($match.Success) {
            $cand = $match.Groups[1].Value.Replace('\\', '\')
            if ($cand -ne '' -and (Test-Path $cand)) { $pptxPath = $cand }
        }
    } catch {}
}

if ($null -eq $pptxPath -and (Test-Path $txtPointer)) {
    try {
        $cand = ([System.IO.File]::ReadAllText($txtPointer, [System.Text.Encoding]::UTF8)).Trim()
        if ($cand -ne '' -and (Test-Path $cand)) { $pptxPath = $cand }
    } catch {}
}

# ---- P2: scan deliveries (8-second budget; PS5.1 manual 2-layer traversal, depth <= 3) ----
if ($null -eq $pptxPath) {
    $deliveriesPath = Join-Path $outputsRoot "deliveries"
    if (Test-Path $deliveriesPath) {
        # Layer 2: immediate subdirs of deliveries/ (newest first, max 50)
        $dirs = Get-ChildItem $deliveriesPath -Directory -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 50

        foreach ($dir in $dirs) {
            if ($sw.Elapsed.TotalSeconds -gt 8) { break }

            # Check *.pptx directly in this subdir (depth 2)
            $f = Get-ChildItem $dir.FullName -Filter "*.pptx" -File -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending |
                Select-Object -First 1
            if ($null -ne $f) { $pptxPath = $f.FullName; break }

            # Layer 3: one level deeper (depth 3)
            $subdirs = Get-ChildItem $dir.FullName -Directory -ErrorAction SilentlyContinue
            foreach ($sub in $subdirs) {
                if ($sw.Elapsed.TotalSeconds -gt 8) { break }
                $f2 = Get-ChildItem $sub.FullName -Filter "*.pptx" -File -ErrorAction SilentlyContinue |
                    Sort-Object LastWriteTime -Descending |
                    Select-Object -First 1
                if ($null -ne $f2) { $pptxPath = $f2.FullName; break }
            }

            if ($null -ne $pptxPath) { break }
        }
    }
}

# ---- P1: LAST resort - root executive_report.pptx (only when P0 and P2 both fail) ----
if ($null -eq $pptxPath) {
    $fallback = Join-Path $outputsRoot "executive_report.pptx"
    if (Test-Path $fallback) { $pptxPath = $fallback }
}

# ---- P3: NOT_FOUND ----
if ($null -eq $pptxPath) {
    if ($sw.Elapsed.TotalSeconds -gt 8) {
        Write-Output "OPEN_PPT: NOT_FOUND (time_budget_exceeded)"
    } else {
        Write-Output "OPEN_PPT: NOT_FOUND"
    }
    exit 2
}

try {
    Start-Process -FilePath $pptxPath -WindowStyle Normal
    Write-Output "OPEN_PPT: OK $pptxPath"
    exit 0
} catch {
    Write-Output "OPEN_PPT: FAIL $_"
    exit 1
}
