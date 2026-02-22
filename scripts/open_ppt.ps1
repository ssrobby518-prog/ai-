# open_ppt.ps1 - Non-blocking PPT opener with pointer + 8-second scan budget
# Priority: P0 pointer file -> P1 deliveries scan -> P2 root executive_report.pptx
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
    Write-Output "OPEN_PPT: NOT_FOUND"
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

# ---- P1: scan deliveries (8-second budget) ----
if ($null -eq $pptxPath) {
    $deliveriesPath = Join-Path $outputsRoot "deliveries"
    if (Test-Path $deliveriesPath) {
        $dirs = Get-ChildItem $deliveriesPath -Directory -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 50
        foreach ($dir in $dirs) {
            if ($sw.Elapsed.TotalSeconds -gt 8) { break }
            $f = Get-ChildItem $dir.FullName -Filter "*.pptx" -File -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending |
                Select-Object -First 1
            if ($null -ne $f) { $pptxPath = $f.FullName; break }
        }
    }
}

# ---- P2: fallback outputs root executive_report.pptx ----
if ($null -eq $pptxPath) {
    $fallback = Join-Path $outputsRoot "executive_report.pptx"
    if (Test-Path $fallback) { $pptxPath = $fallback }
}

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
