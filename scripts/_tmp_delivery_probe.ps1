$root = Split-Path $PSScriptRoot -Parent
Write-Host "REPO: $root"

$ep = Join-Path $root "outputs\executive_report.pptx"
Write-Host "EP_EXISTS: $(Test-Path $ep)"
if (Test-Path $ep) { Write-Host "EP_TIME: $((Get-Item $ep).LastWriteTime)" }

$d = Join-Path $root "outputs\deliveries"
Write-Host "DELIVERIES_DIR_EXISTS: $(Test-Path $d)"

$dirs = Get-ChildItem $d -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Desc | Select-Object -First 5
Write-Host "DIR_COUNT: $($dirs.Count)"
foreach ($dir in $dirs) {
    Write-Host "  DIR: $($dir.Name)  $($dir.LastWriteTime)"
    $allFiles = Get-ChildItem $dir.FullName -ErrorAction SilentlyContinue
    Write-Host "  FILE_COUNT: $($allFiles.Count)"
    foreach ($f in $allFiles | Select-Object -First 5) {
        Write-Host "    $($f.Name)  $($f.Length)B"
    }
}

# Also check deliveries root for loose pptx
$loosePptx = Get-ChildItem $d -Filter "*.pptx" -ErrorAction SilentlyContinue
Write-Host "LOOSE_PPTX_IN_DELIVERIES_ROOT: $($loosePptx.Count)"

# Check outputs root for all pptx
$outPptx = Get-ChildItem (Join-Path $root "outputs") -Filter "*.pptx" -ErrorAction SilentlyContinue
Write-Host "PPTX_IN_OUTPUTS: $($outPptx.Count)"
foreach ($f in $outPptx | Sort-Object LastWriteTime -Desc | Select-Object -First 5) {
    Write-Host "  $($f.Name)  $($f.LastWriteTime)"
}
