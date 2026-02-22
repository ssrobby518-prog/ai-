# write_latest_delivery_pointer.ps1
# Scans deliveries/ for newest PPTX; writes latest_delivery.txt + latest_delivery.json.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\write_latest_delivery_pointer.ps1

$repoRoot = Split-Path $PSScriptRoot -Parent

$R0 = Join-Path $repoRoot "outputs"
$R1 = Join-Path (Split-Path $repoRoot -Parent) "outputs"

$outputsRoot = $null
foreach ($candidate in @($R0, $R1)) {
    if (Test-Path $candidate) { $outputsRoot = $candidate; break }
}

if ($null -eq $outputsRoot) {
    Write-Output "LATEST_POINTER: NOT_FOUND (outputs root not found)"
    exit 2
}

$pptxPath = $null
$deliveriesPath = Join-Path $outputsRoot "deliveries"

if (Test-Path $deliveriesPath) {
    # Scan newest 50 subdirs; stop at first PPTX found (no -Recurse over full tree)
    $dirs = Get-ChildItem $deliveriesPath -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 50

    foreach ($dir in $dirs) {
        $f = Get-ChildItem $dir.FullName -Filter "*.pptx" -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($null -ne $f) {
            $pptxPath = $f.FullName
            break
        }
    }
}

# Fallback: outputs root executive_report.pptx
if ($null -eq $pptxPath) {
    $fallback = Join-Path $outputsRoot "executive_report.pptx"
    if (Test-Path $fallback) { $pptxPath = $fallback }
}

if ($null -eq $pptxPath) {
    Write-Output "LATEST_POINTER: NOT_FOUND"
    exit 2
}

$txtPath  = Join-Path $outputsRoot "latest_delivery.txt"
$jsonPath = Join-Path $outputsRoot "latest_delivery.json"

# Write txt: single line, UTF-8 no BOM
[System.IO.File]::WriteAllText($txtPath, $pptxPath, [System.Text.UTF8Encoding]::new($false))

# Write json: minimal, UTF-8 no BOM
$genAt       = [datetime]::Now.ToString("yyyy-MM-ddTHH:mm:ss")
$escapedPath = $pptxPath.Replace('\', '\\')
$jsonContent = "{`"generated_at`":`"$genAt`",`"pptx_path`":`"$escapedPath`"}"
[System.IO.File]::WriteAllText($jsonPath, $jsonContent, [System.Text.UTF8Encoding]::new($false))

Write-Output "LATEST_POINTER: OK $pptxPath"
