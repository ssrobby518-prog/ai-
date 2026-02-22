$root = Split-Path $PSScriptRoot -Parent
Write-Host "REPO: $root"

$ep = Join-Path $root "outputs\executive_report.pptx"
Write-Host "EP_EXISTS: $(Test-Path $ep)"
if (Test-Path $ep) { Write-Host "EP_TIME: $((Get-Item $ep).LastWriteTime)" }

$d = Join-Path $root "outputs\deliveries"
Write-Host "DELIVERIES_EXISTS: $(Test-Path $d)"

$dirs = Get-ChildItem $d -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Desc | Select-Object -First 5
Write-Host "DELIVERY_DIRS: $($dirs.Count)"
foreach ($dir in $dirs) {
    Write-Host "  DIR: $($dir.Name)"
    $pptxs = Get-ChildItem $dir.FullName -Filter "*.pptx" -ErrorAction SilentlyContinue
    Write-Host "  PPTX_COUNT: $($pptxs.Count)"
    foreach ($p in $pptxs) { Write-Host "    $($p.Name)  $($p.LastWriteTime)" }
}

$ljson = Join-Path $root "outputs\latest_delivery.json"
$ltxt  = Join-Path $root "outputs\latest_delivery.txt"
Write-Host "LATEST_JSON: $(Test-Path $ljson)"
Write-Host "LATEST_TXT: $(Test-Path $ltxt)"
