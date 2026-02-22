$d = 'C:\Projects\ai捕捉資訊\ai-intel-scraper-mvp\outputs\deliveries'
$dirs = Get-ChildItem $d -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Desc | Select-Object -First 5
foreach ($dir in $dirs) {
    Write-Host ("DIR: " + $dir.Name + "  " + $dir.LastWriteTime)
    $pptxs = Get-ChildItem $dir.FullName -Filter "*.pptx" -ErrorAction SilentlyContinue
    foreach ($p in $pptxs) { Write-Host ("  PPTX: " + $p.Name + "  " + $p.LastWriteTime) }
}
Write-Host "---executive_report.pptx---"
$ep = 'C:\Projects\ai捕捉資訊\ai-intel-scraper-mvp\outputs\executive_report.pptx'
if (Test-Path $ep) { (Get-Item $ep).LastWriteTime }
Write-Host "---latest_delivery check---"
Test-Path 'C:\Projects\ai捕捉資訊\ai-intel-scraper-mvp\outputs\latest_delivery.json'
Test-Path 'C:\Projects\ai捕捉資訊\ai-intel-scraper-mvp\outputs\latest_delivery.txt'
