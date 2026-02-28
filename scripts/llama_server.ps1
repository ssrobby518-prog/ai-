# scripts/llama_server.ps1 — Start llama-server.exe (llama.cpp OpenAI-compatible API)
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\llama_server.ps1
#
# Starts llama-server.exe at http://127.0.0.1:8080 (or $env:LLAMA_PORT if set).
# If already running and responsive, does nothing.

$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# NOTE: Use junction path C:\llama_node (no Unicode) because llama-server.exe
# uses ANSI file APIs that cannot handle Chinese characters in paths.
# Create junction once: mklink /J C:\llama_node "C:\Projects\ai捕捉資訊\qwen_inference_node_4060"
$llamaExe  = "C:\llama_node\llama-b8123-bin-win-cuda-12.4-x64\llama-server.exe"
$modelPath = "C:\llama_node\Qwen2.5-7B-Instruct-Q4_K_M\Qwen2.5-7B-Instruct-Q4_K_M.gguf"
$host_ip   = "127.0.0.1"
$port      = if ($env:LLAMA_PORT) { [int]$env:LLAMA_PORT } else { 8080 }
$baseUrl   = "http://${host_ip}:${port}"

Write-Output "=== llama_server.ps1 ==="
Write-Output ("llama-server  : {0}" -f $llamaExe)
Write-Output ("model         : {0}" -f $modelPath)
Write-Output ("endpoint      : {0}" -f $baseUrl)
Write-Output ""

# ── 1. Check if already running ────────────────────────────────────────────
Write-Output "Step 1: Checking if llama-server is already responsive..."
try {
    $resp = Invoke-RestMethod -Uri "${baseUrl}/v1/models" -TimeoutSec 4 -ErrorAction Stop
    Write-Output "  llama-server already running and responsive."
    Write-Output ("  Models: {0}" -f (($resp.data | ForEach-Object { $_.id }) -join ', '))
    Write-Output ""
    Write-Output "llama_server.ps1 DONE (already running)"
    exit 0
} catch {
    Write-Output "  Not running yet; will start."
}

# ── 2. Verify files exist ──────────────────────────────────────────────────
Write-Output ""
Write-Output "Step 2: Verifying file paths..."
if (-not (Test-Path $llamaExe)) {
    Write-Output ("ERROR: llama-server.exe not found: {0}" -f $llamaExe)
    exit 1
}
if (-not (Test-Path $modelPath)) {
    Write-Output ("ERROR: GGUF model not found: {0}" -f $modelPath)
    exit 1
}
Write-Output "  OK"

# ── 3. Free port if occupied ────────────────────────────────────────────────
Write-Output ""
Write-Output ("Step 3: Checking port {0}..." -f $port)
$portConn = netstat -ano 2>$null | Select-String ":${port}\s"
if ($portConn) {
    Write-Output ("  Port {0} in use; attempting to release..." -f $port)
    $portConn | ForEach-Object {
        if ($_ -match "\s+(\d+)$") {
            $pid_ = [int]$Matches[1]
            try { Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
    Start-Sleep -Seconds 2
}

# ── 4. Build argument list ─────────────────────────────────────────────────
# GPU-first defaults (Iteration GPU-v1):
#   --n-gpu-layers -1  = offload ALL layers to GPU (full VRAM offload)
#   --main-gpu 0       = use GPU index 0 (single-card)
#   --ctx-size 8192    = larger context keeps GPU fed
#   --batch-size 512   = larger batch improves GPU throughput
# Override via env: LLAMA_N_GPU_LAYERS, LLAMA_CTX_SIZE
$nGpuLayers = if ($env:LLAMA_N_GPU_LAYERS) { [int]$env:LLAMA_N_GPU_LAYERS } else { -1 }
$ctxSize    = if ($env:LLAMA_CTX_SIZE)     { [int]$env:LLAMA_CTX_SIZE }     else { 8192 }

$args_ = @(
    "--model",        $modelPath,
    "--host",         $host_ip,
    "--port",         $port,
    "--ctx-size",     $ctxSize,
    "--n-gpu-layers", $nGpuLayers,
    "--main-gpu",     0,
    "--threads",      8,
    "--batch-size",   512,
    "--parallel",     1
)

# --no-webui may not exist in all builds; try with it first
$args_noweb = $args_ + @("--no-webui")

# ── 5. Start server ────────────────────────────────────────────────────────
Write-Output ""
Write-Output "=== GPU LAUNCH CONFIG EVIDENCE ==="
Write-Output ("  --n-gpu-layers : {0}  (target: -1 = all layers offloaded to GPU)" -f $nGpuLayers)
Write-Output ("  --main-gpu     : 0   (GPU index 0)")
Write-Output ("  --ctx-size     : {0}" -f $ctxSize)
Write-Output ("  --batch-size   : 512")
Write-Output ("  backend        : CUDA (llama-b8123-bin-win-cuda-12.4-x64)")
Write-Output ("  model          : {0}" -f $modelPath)
Write-Output "==================================="
Write-Output ""
Write-Output ("Step 4: Starting llama-server (n-gpu-layers={0} ctx={1})..." -f $nGpuLayers, $ctxSize)

$proc = $null
try {
    $proc = Start-Process -FilePath $llamaExe -ArgumentList $args_noweb -PassThru -WindowStyle Hidden -ErrorAction Stop
    Write-Output ("  Started PID {0} (with --no-webui)" -f $proc.Id)
} catch {
    Write-Output "  --no-webui not supported; retrying without it..."
    try {
        $proc = Start-Process -FilePath $llamaExe -ArgumentList $args_ -PassThru -WindowStyle Hidden -ErrorAction Stop
        Write-Output ("  Started PID {0}" -f $proc.Id)
    } catch {
        Write-Output ("ERROR: Failed to start llama-server: {0}" -f $_)
        exit 1
    }
}

# ── 6. Wait for API readiness (up to 90 seconds) ────────────────────────────
Write-Output ""
Write-Output "Step 5: Waiting for API to become ready..."
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 3
    try {
        $resp2 = Invoke-RestMethod -Uri "${baseUrl}/v1/models" -TimeoutSec 4 -ErrorAction Stop
        $modelIds = ($resp2.data | ForEach-Object { $_.id }) -join ', '
        Write-Output ("  API ready after {0}s  models: {1}" -f ($i * 3 + 3), $modelIds)
        $ready = $true
        break
    } catch {
        Write-Output ("  Waiting... ({0}s)" -f ($i * 3 + 3))
    }
}

if (-not $ready) {
    Write-Output ""
    Write-Output "ERROR: llama-server did not become ready within 90 seconds."
    Write-Output "  Check GPU memory; try reducing n-gpu-layers via LLAMA_N_GPU_LAYERS env var."
    Write-Output ("  Current: --n-gpu-layers {0}  --ctx-size {1}" -f $nGpuLayers, $ctxSize)
    if ($proc) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    exit 1
}

Write-Output ""
Write-Output ("llama_server.ps1 DONE  PID={0}  URL={1}" -f $proc.Id, $baseUrl)
