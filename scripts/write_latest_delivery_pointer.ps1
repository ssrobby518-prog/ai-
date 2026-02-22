# write_latest_delivery_pointer.ps1
# Scans deliveries/ for newest PPTX; writes latest_delivery.txt + latest_delivery.json.
# PS5.1 compatible: no -Depth flag; manual 2-layer directory traversal (depth <= 3).
# 2-second Stopwatch budget (exits scan early if exceeded).
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\write_latest_delivery_pointer.ps1

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$repoRoot = Split-Path $PSScriptRoot -Parent

$R0 = Join-Path $repoRoot "outputs"
$R1 = Join-Path (Split-Path $repoRoot -Parent) "outputs"

$outputsRoot = $null
foreach ($candidate in @($R0, $R1)) {
    if (Test-Path $candidate) { $outputsRoot = $candidate; break }
}

if ($null -eq $outputsRoot) {
    Write-Output "LATEST_DELIVERY: NOT_FOUND (outputs root not found)"
    exit 2
}

$pptxPath     = $null
$deliveriesPath = Join-Path $outputsRoot "deliveries"

if (Test-Path $deliveriesPath) {
    # Layer 2: immediate subdirs of deliveries/ (newest first, max 50)
    $dirs = Get-ChildItem $deliveriesPath -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 50

    foreach ($dir in $dirs) {
        if ($sw.Elapsed.TotalSeconds -gt 2) { break }

        # Check *.pptx directly inside this subdir (depth 2)
        $f = Get-ChildItem $dir.FullName -Filter "*.pptx" -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($null -ne $f) { $pptxPath = $f.FullName; break }

        # Layer 3: one level deeper (depth 3)
        $subdirs = Get-ChildItem $dir.FullName -Directory -ErrorAction SilentlyContinue
        foreach ($sub in $subdirs) {
            if ($sw.Elapsed.TotalSeconds -gt 2) { break }
            $f2 = Get-ChildItem $sub.FullName -Filter "*.pptx" -File -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending |
                Select-Object -First 1
            if ($null -ne $f2) { $pptxPath = $f2.FullName; break }
        }

        if ($null -ne $pptxPath) { break }
    }
}

if ($null -eq $pptxPath) {
    Write-Output "LATEST_DELIVERY: NOT_FOUND"
    exit 2
}

$txtPath  = Join-Path $outputsRoot "latest_delivery.txt"
$jsonPath = Join-Path $outputsRoot "latest_delivery.json"

# Write txt: single line, UTF-8 no BOM
[System.IO.File]::WriteAllText($txtPath, $pptxPath, [System.Text.UTF8Encoding]::new($false))

# Write json: minimal, UTF-8 no BOM
$genAt       = [datetime]::Now.ToString("yyyy-MM-ddTHH:mm:ss")
$escapedPath = $pptxPath.Replace('\', '\\')
$elapsed     = [math]::Round($sw.Elapsed.TotalSeconds, 3)
$jsonContent = "{`"generated_at`":`"$genAt`",`"pptx_path`":`"$escapedPath`",`"scan_seconds`":$elapsed}"
[System.IO.File]::WriteAllText($jsonPath, $jsonContent, [System.Text.UTF8Encoding]::new($false))

Write-Output "LATEST_DELIVERY: OK $pptxPath"
Write-Output "SCAN_TIME: $($elapsed)s"
