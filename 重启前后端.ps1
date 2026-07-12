<#
.SYNOPSIS
    Hardware RAG Agent — 一键重启前端 + 后端脚本
.DESCRIPTION
    - 自动停掉运行中的 backend (main.py --web --port 58080) 和 frontend (vite --port 5173)
    - 重新拉起后端 + 前端，各自在新窗口运行（避免互相串日志）
    - 停止逻辑只匹配命令行包含 "main.py --web" 或 "vite" 的进程，不会误杀其他 python/node 进程
.PARAMETER Only
    可选：只重启某一端。取值 backend / frontend / all（默认 all）
.PARAMETER BackendPort
    后端端口，默认 58080
.PARAMETER FrontendPort
    前端端口，默认 5173
.EXAMPLE
    .\重启前后端.ps1
    重启前后端（默认全部）
.EXAMPLE
    .\重启前后端.ps1 -Only backend
    只重启后端
.EXAMPLE
    .\重启前后端.ps1 -Only frontend
    只重启前端
.NOTES
    使用方法见 docs/pitfalls.md 同级目录的 README 或直接看 .SYNOPSIS
    脚本路径：e:\Desktop\agent\重启前后端.ps1
#>

[CmdletBinding()]
param(
    [ValidateSet('all', 'backend', 'frontend')]
    [string]$Only = 'all',

    [int]$BackendPort = 58080,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step($msg) { Write-Host "`n[*] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "[!]  $msg" -ForegroundColor Yellow }

function Stop-ProcessByCmdline {
    param([string]$Pattern)
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$Pattern*" }
    if (-not $procs) {
        Write-Warn2 "未找到匹配进程: $Pattern"
        return
    }
    foreach ($p in $procs) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Write-Ok "已停止 PID $($p.ProcessId)  ($Pattern)"
        } catch {
            Write-Warn2 "停止失败 PID $($p.ProcessId): $($_.Exception.Message)"
        }
    }
    Start-Sleep -Seconds 1
}

function Start-Backend {
    Write-Step "启动后端 (port=$BackendPort)"
    $backendDir = Join-Path $ProjectRoot 'backend'
    if (-not (Test-Path (Join-Path $backendDir 'main.py'))) {
        Write-Warn2 "未找到 backend\main.py，跳过"
        return
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo 'powershell'
    $psi.Arguments = "-NoExit -Command `"cd '$backendDir'; python main.py --web --port $BackendPort`""
    $psi.UseShellExecute = $true
    $psi.WindowStyle = 'Normal'
    $psi.WorkingDirectory = $backendDir
    [void][System.Diagnostics.Process]::Start($psi)
    Write-Ok "后端已在新窗口启动: http://127.0.0.1:$BackendPort"
}

function Start-Frontend {
    Write-Step "启动前端 (port=$FrontendPort)"
    $frontendDir = Join-Path $ProjectRoot 'frontend'
    if (-not (Test-Path (Join-Path $frontendDir 'package.json'))) {
        Write-Warn2 "未找到 frontend\package.json，跳过"
        return
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo 'powershell'
    $psi.Arguments = "-NoExit -Command `"cd '$frontendDir'; npx vite --port $FrontendPort`""
    $psi.UseShellExecute = $true
    $psi.WindowStyle = 'Normal'
    $psi.WorkingDirectory = $frontendDir
    [void][System.Diagnostics.Process]::Start($psi)
    Write-Ok "前端已在新窗口启动: http://127.0.0.1:$FrontendPort"
}

# ── 主流程 ──────────────────────────────────
Write-Step "Hardware RAG Agent 重启脚本 (mode=$Only)"
Write-Host "项目根目录: $ProjectRoot"

if ($Only -eq 'all' -or $Only -eq 'backend') {
    Write-Step "停止旧后端..."
    Stop-ProcessByCmdline 'main.py --web'
}

if ($Only -eq 'all' -or $Only -eq 'frontend') {
    Write-Step "停止旧前端..."
    Stop-ProcessByCmdline 'vite'
}

if ($Only -eq 'all' -or $Only -eq 'backend') { Start-Backend }
if ($Only -eq 'all' -or $Only -eq 'frontend') { Start-Frontend }

Write-Step "完成"
Write-Host "  后端: http://127.0.0.1:$BackendPort"
Write-Host "  前端: http://127.0.0.1:$FrontendPort"
Write-Host "  API 文档: http://127.0.0.1:$BackendPort/docs"
Write-Host ""
Write-Host "  关闭窗口即停止对应服务" -ForegroundColor DarkGray
