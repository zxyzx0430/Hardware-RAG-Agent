<#
.SYNOPSIS
  Hardware RAG Agent — 一键启动前后端开发环境
.DESCRIPTION
  后端固定 :8000，前端固定 :5173。自动检测端口占用、清理旧进程、等待后端就绪。
  按 Ctrl+C 停止所有服务。
#>

$ROOT = Split-Path -Parent $PSScriptRoot
$BE_PORT = 8000
$FE_PORT = 5173

function step  { Write-Host "`n==> $args" -ForegroundColor Cyan }
function info  { Write-Host "  $args" -ForegroundColor Green }
function warn  { Write-Host "  !! $args" -ForegroundColor Yellow }
function err   { Write-Host "  XX $args" -ForegroundColor Red; exit 1 }

# ---- 1. 环境检查 ----
step "1/4 环境检查"
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { err "未找到 Python，请安装 Python 3.10+" }
$pv = python --version
info "Python: $pv"

$node = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $node) { err "未找到 Node.js，请安装 Node.js 18+" }
$nv = node --version
info "Node:   $nv"

# ---- 2. 端口检查 ----
step "2/4 端口检查"
$beBusy = Get-NetTCPConnection -LocalPort $BE_PORT -ErrorAction SilentlyContinue
if ($beBusy) {
    warn "端口 $BE_PORT 被占用 (PID $($beBusy.OwningProcess))，正在关闭..."
    Stop-Process -Id $beBusy.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep 2
}
info "后端端口 $BE_PORT 可用"

$feBusy = Get-NetTCPConnection -LocalPort $FE_PORT -ErrorAction SilentlyContinue
if ($feBusy) {
    warn "端口 $FE_PORT 被占用 (PID $($feBusy.OwningProcess))，正在关闭..."
    Stop-Process -Id $feBusy.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep 2
}
info "前端端口 $FE_PORT 可用"

# ---- 3. 启动后端 ----
step "3/4 启动后端 (127.0.0.1:$BE_PORT)"
$env:HOST = "127.0.0.1"
$env:PORT = "$BE_PORT"
$env:LOG_LEVEL = "INFO"
$BE_LOG = Join-Path $ROOT "backend_dev.log"
$BE_PROC = Start-Process -NoNewWindow -PassThru -FilePath "cmd" -ArgumentList "/c", "python", "-m", "app.main" -WorkingDirectory (Join-Path $ROOT "backend") -RedirectStandardOutput $BE_LOG
info "PID $($BE_PROC.Id)  日志: backend_dev.log"

info "等待后端就绪..."
$healthy = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep 1
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$BE_PORT/health" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { $healthy = $true; break }
    } catch {}
}
if (-not $healthy) { err "后端启动超时 (30s)，查看 backend_dev.log" }
info "就绪  http://127.0.0.1:$BE_PORT"
info "文档  http://127.0.0.1:$BE_PORT/docs"

# ---- 4. 启动前端 ----
step "4/4 启动前端 (127.0.0.1:$FE_PORT)"
$FE_LOG = Join-Path $ROOT "frontend_dev.log"
$FE_PROC = Start-Process -NoNewWindow -PassThru -FilePath "cmd" -ArgumentList "/c", "npx", "vite", "--host", "127.0.0.1", "--port", "$FE_PORT" -WorkingDirectory (Join-Path $ROOT "frontend") -RedirectStandardOutput $FE_LOG
info "PID $($FE_PROC.Id)  日志: frontend_dev.log"

Start-Sleep 4
$feUp = Get-NetTCPConnection -LocalPort $FE_PORT -ErrorAction SilentlyContinue
if (-not $feUp) { warn "前端可能未启动，查看 frontend_dev.log" }
else { info "就绪  http://127.0.0.1:$FE_PORT" }

# ---- 输出 ----
Write-Host @"

  +-------------------------------------------+
  |  Hardware RAG Agent                       |
  +-------------------------------------------+
  |  前端  http://127.0.0.1:$FE_PORT          |
  |  后端  http://127.0.0.1:$BE_PORT          |
  |  API   http://127.0.0.1:$BE_PORT/docs     |
  +-------------------------------------------+

"@ -ForegroundColor Cyan
Write-Host "按 Ctrl+C 停止服务" -ForegroundColor Yellow

# ---- 等待退出 ----
try {
    while ($true) { Start-Sleep 5 }
} finally {
    step "正在停止服务..."
    Stop-Process -Id $BE_PROC.Id -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $FE_PROC.Id -Force -ErrorAction SilentlyContinue
    info "已停止"
}
