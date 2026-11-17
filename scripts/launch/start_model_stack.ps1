<#
.SYNOPSIS
    启动 / 查询 / 停止 WSL2 里的 llama.cpp 模型服务（:8000 对话, :8001 Agent）。

.DESCRIPTION
    本机部署用这个脚本管理模型服务，生产机仍然用 scripts\engines\llama_cpp
    下自己的启动脚本。

    两个关键事实决定了这里的做法：
      1. 模型服务跑在 WSL2 里，llama-server 是 Linux 二进制；
      2. WSL2 在没有会话时会回收整个发行版，setsid 也留不住进程。
    所以这里用"保活进程"的方式启动：一个隐藏的 wsl.exe 始终保持会话不退出，
    llama-server 得以常驻；停服务时再把这个保活进程一起收掉。

    默认绑定 0.0.0.0：本机 WSL 的 localhost 转发只对通配地址生效，
    绑 127.0.0.1 时 Windows 侧连不上。WSL 的 NAT 网段在物理局域网内不可路由，
    因此这不等于把服务暴露到网络。生产机请用 -BindLoopback 保持原样。

.PARAMETER Status
    只报告 :8000 / :8001 状态。

.PARAMETER Stop
    停止 llama-server 与保活进程。

.PARAMETER Distro
    WSL 发行版名，缺省取 wsl -l -q 的第一个。

.PARAMETER WaitSeconds
    等待模型加载的最长秒数，默认 180。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\launch\start_model_stack.ps1 -Status
#>
[CmdletBinding()]
param(
    [switch]$Status,
    [switch]$Stop,
    [string]$Distro = '',
    [int]$WaitSeconds = 180,
    [switch]$BindLoopback
)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:PidFile = Join-Path $script:Root 'logs\wsl_stack.pid'
$script:LogDir = Join-Path $script:Root 'logs'

function Test-StackPort([int]$Port) {
    try {
        $resp = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/v1/models" -f $Port) `
            -UseBasicParsing -TimeoutSec 4
        if ($resp.StatusCode -ne 200) { return $null }
        $payload = $resp.Content | ConvertFrom-Json
        $ids = @()
        foreach ($item in @($payload.data)) { if ($item.id) { $ids += $item.id } }
        return ($ids -join ',')
    } catch {
        return $null
    }
}

function Show-Status {
    $dialogue = Test-StackPort 8000
    $agent = Test-StackPort 8001
    if ($dialogue) { Write-Host ("  [OK] :8000 对话  {0}" -f $dialogue) -ForegroundColor Green }
    else { Write-Host '  [--] :8000 对话  未就绪' -ForegroundColor Red }
    if ($agent) { Write-Host ("  [OK] :8001 Agent {0}" -f $agent) -ForegroundColor Green }
    else { Write-Host '  [--] :8001 Agent 未就绪' -ForegroundColor Red }
    return [bool]($dialogue -and $agent)
}

function Resolve-Distro {
    if ($Distro) { return $Distro }
    try {
        $raw = & wsl -l -q 2>$null
        foreach ($line in $raw) {
            $name = ($line -replace "`0", '').Trim()
            if ($name) { return $name }
        }
    } catch { }
    return ''
}

function ConvertTo-WslPath([string]$WindowsPath) {
    # D:\voicechat3 -> /mnt/d/voicechat3 ; the repo may live on any drive.
    $p = $WindowsPath -replace '\\', '/'
    if ($p -match '^([A-Za-z]):(.*)$') {
        $drive = $Matches[1].ToLower()
        $rest = $Matches[2]
        return "/mnt/$drive$rest"
    }
    return $p
}

if ($Stop) {
    $distro = Resolve-Distro
    if ($distro) {
        & wsl -d $distro -- bash -lc "bash /root/vc_stack.sh --stop" 2>$null | Out-Null
    }
    if (Test-Path $script:PidFile) {
        $keeperPid = (Get-Content $script:PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($keeperPid) {
            $proc = Get-Process -Id ([int]$keeperPid) -ErrorAction SilentlyContinue
            if ($proc) {
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                Write-Host ("  已结束保活进程 PID {0}" -f $proc.Id) -ForegroundColor Green
            }
        }
        Remove-Item $script:PidFile -Force -ErrorAction SilentlyContinue
    }
    Write-Host '  模型服务已停止。' -ForegroundColor DarkGray
    exit 0
}

if ($Status) {
    if (Show-Status) { exit 0 } else { exit 1 }
}

if (-not (Test-Path $script:LogDir)) { New-Item -ItemType Directory -Force -Path $script:LogDir | Out-Null }

$distro = Resolve-Distro
if (-not $distro) {
    Write-Host '  未找到 WSL 发行版。' -ForegroundColor Red
    exit 2
}

$keeperAlive = $false
if (Test-Path $script:PidFile) {
    $keeperPid = (Get-Content $script:PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($keeperPid -and (Get-Process -Id ([int]$keeperPid) -ErrorAction SilentlyContinue)) {
        $keeperAlive = $true
    }
}

if ((Show-Status) -and $keeperAlive) {
    Write-Host '  模型服务已在运行（且有保活进程），无需重复启动。' -ForegroundColor DarkGray
    exit 0
}

$wslStack = ConvertTo-WslPath (Join-Path $script:Root 'scripts\engines\llama_cpp\start_stack_wsl.sh')
$bind = '127.0.0.1'
if (-not $BindLoopback) { $bind = '0.0.0.0' }

# The repo script runs straight from /mnt (no copy needed: .gitattributes pins
# *.sh to LF, so bash reads it fine). The trailing sleep is the keeper: without
# it WSL tears the distro down and every llama-server dies with it.
$inner = "HOST=$bind bash '$wslStack' >> /root/vc_stack_boot.log 2>&1; sleep 86400"

Write-Host ("  启动模型服务（WSL: {0}, bind {1}）..." -f $distro, $bind) -ForegroundColor Yellow
$proc = Start-Process -FilePath 'wsl.exe' `
    -ArgumentList @('-d', $distro, '--', 'bash', '-lc', $inner) `
    -WindowStyle Hidden -PassThru
if ($proc) { Set-Content -Path $script:PidFile -Value $proc.Id -Encoding ascii }

$deadline = (Get-Date).AddSeconds($WaitSeconds)
$ready = $false
while ((Get-Date) -lt $deadline) {
    $dialogue = Test-StackPort 8000
    $agent = Test-StackPort 8001
    if ($dialogue -and $agent) { $ready = $true; break }
    Start-Sleep -Seconds 3
}

if ($ready) {
    Show-Status | Out-Null
    Write-Host ("  模型服务就绪：8000={0}  8001={1}" -f (Test-StackPort 8000), (Test-StackPort 8001)) -ForegroundColor Green
    exit 0
}

Write-Host '  模型服务未在规定时间内就绪。' -ForegroundColor Red
Write-Host '  排查：wsl -d ' -NoNewline
Write-Host $distro -NoNewline
Write-Host ' -- bash -lc "tail -40 /root/llama_8000.log"'
exit 3
