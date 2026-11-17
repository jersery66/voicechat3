<#
.SYNOPSIS
    启动 / 查询 / 停止 WSL2 里的 llama.cpp 模型服务（:8000 对话, :8001 Agent）。

.DESCRIPTION
    真正的实现在 deployment\autostart.py 里，应用启动时（main.py）用的是同一份逻辑，
    这里只是给习惯用脚本的人一个入口。

    两个关键事实决定了那里的做法：
      1. 模型服务跑在 WSL2 里，llama-server 是 Linux 二进制；
      2. WSL2 在没有会话时会回收整个发行版，setsid 也留不住进程。
    所以用一个 detach 的 wsl.exe 会话保持存活，llama-server 才能常驻。

    默认绑 0.0.0.0：本机 WSL 的 localhost 转发只对通配地址生效，绑 127.0.0.1 时
    Windows 侧连不上。WSL 的 NAT 网段在物理局域网内不可路由，因此不等于对外暴露。

.PARAMETER Status
    只报告 :8000 / :8001 状态。

.PARAMETER Stop
    停止 llama-server 与保活会话。

.PARAMETER WaitSeconds
    等待模型加载的最长秒数，默认 180。

.PARAMETER BindLoopback
    绑 127.0.0.1（仅当本机 localhost 转发正常时用，生产机保持原样）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\launch\start_model_stack.ps1 -Status
#>
[CmdletBinding()]
param(
    [switch]$Status,
    [switch]$Stop,
    [int]$WaitSeconds = 180,
    [switch]$BindLoopback
)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }

$env:PYTHONPATH = $root
$bind = '0.0.0.0'
if ($BindLoopback) { $bind = '127.0.0.1' }

if ($Status) {
    & $py -m deployment.autostart --status
    exit $LASTEXITCODE
}

if ($Stop) {
    & $py -m deployment.autostart --stop-services
    exit $LASTEXITCODE
}

& $py -m deployment.autostart --start-services --wait $WaitSeconds --bind $bind
exit $LASTEXITCODE
