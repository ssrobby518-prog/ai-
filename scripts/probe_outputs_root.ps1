# probe_outputs_root.ps1 - Detect correct outputs root (R0 inside repo, R1 parent)
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\probe_outputs_root.ps1

$repoRoot = Split-Path $PSScriptRoot -Parent

$R0 = Join-Path $repoRoot "outputs"
$R1 = Join-Path (Split-Path $repoRoot -Parent) "outputs"

$outputsRoot = $null
foreach ($candidate in @($R0, $R1)) {
    if (Test-Path $candidate) {
        $outputsRoot = $candidate
        break
    }
}

if ($null -eq $outputsRoot) {
    Write-Output "OUTPUTS_ROOT: NOT_FOUND"
    exit 3
}

$deliveriesPath = Join-Path $outputsRoot "deliveries"
$deliveriesExists = Test-Path $deliveriesPath

# Check for exec pptx: first inside newest delivery subdir, then in outputs root
$execPptExists = $false
if ($deliveriesExists) {
    $newestDir = Get-ChildItem $deliveriesPath -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($newestDir) {
        $pptxInDir = Get-ChildItem $newestDir.FullName -Filter "*.pptx" -File -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -ne $pptxInDir) { $execPptExists = $true }
    }
}
if (-not $execPptExists) {
    $execPptExists = Test-Path (Join-Path $outputsRoot "executive_report.pptx")
}

Write-Output "OUTPUTS_ROOT: $outputsRoot"
Write-Output "DELIVERIES_EXISTS: $deliveriesExists"
Write-Output "EXEC_PPT_EXISTS: $execPptExists"
