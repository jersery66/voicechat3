<#
.SYNOPSIS
    本机部署启动器：自动适配这台机器的 Python / 依赖 / 模型服务 / 路径。

.DESCRIPTION
    与生产用的 bootstrap.ps1 分开，互不影响：
      bootstrap.ps1        面向实验机（固定 5090 + llama.cpp + 本地权重，strict 预检）
      bootstrap_local.ps1  面向"任意一台机器"（本机没有权重时自动降级到离线桩）

    本脚本按顺序做：
      1. 找 Python 3.10+，必要时建 .venv
      2. 检查依赖，缺什么装什么（清单在 deployment\dependencies.py；
         torch 按显卡自动选 CUDA 通道，没有 N 卡就装 CPU 版）
      3. 探测本机已有的 /v1 模型服务、ASR/TTS 权重、音色提示
      4. 服务没起来就拉 WSL 模型栈；权重缺失时按需下载；再生成运行配置
      5. 用生成的 config\runtime.local.yaml 启动图形界面

    生成的 config\runtime.local.yaml 只是本机的，不入库；
    生产的 config\runtime.yaml 一个字节都不改。

.PARAMETER CheckOnly
    只自检和生成配置，不启动界面。

.PARAMETER NoRepair
    只报告，不改磁盘（不建 venv、不装依赖、不写配置）。

.PARAMETER NoStub
    即使没有模型服务，也不拉起离线桩（此时界面会因连不上模型而拒绝启动）。

.PARAMETER TextOnly
    只装界面必需依赖，跳过 torch/funasr/voxcpm（纯文字使用）。

.PARAMETER ProvisionModels
    缺少语音权重时自动下载（FunASR ≈2GB、VoxCPM2 ≈4.7GB，从 ModelScope）。

.PARAMETER FullRepair
    兼容保留：现在默认就会把语音依赖补齐，此开关等同于默认行为。

.PARAMETER Config
    指定 runtime 配置；缺省用自动生成的 config\runtime.local.yaml。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\launch\bootstrap_local.ps1 -CheckOnly
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$NoRepair,
    [switch]$NoStub,
    [switch]$TextOnly,
    [switch]$ProvisionModels,
    [switch]$FullRepair,
    [string]$Config = ''
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:VenvPython = Join-Path $script:Root '.venv\Scripts\python.exe'
$script:LocalConfig = Join-Path $script:Root 'config\runtime.local.yaml'
$script:Checks = New-Object System.Collections.ArrayList
$script:PyExe = $null

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host (">> " + $Text) -ForegroundColor Cyan
}

function Add-Check([string]$Status, [string]$Name, [string]$Detail = '') {
    $null = $script:Checks.Add([pscustomobject]@{ Status = $Status; Name = $Name; Detail = $Detail })
    $tag = '[  ]'; $color = 'Gray'
    switch ($Status) {
        'OK'   { $tag = '[OK]'; $color = 'Green' }
        'WARN' { $tag = '[!!]'; $color = 'Yellow' }
        'FAIL' { $tag = '[--]'; $color = 'Red' }
        'SKIP' { $tag = '[..]'; $color = 'DarkGray' }
    }
    if ($Detail) { Write-Host ("  {0} {1} - {2}" -f $tag, $Name, $Detail) -ForegroundColor $color }
    else { Write-Host ("  {0} {1}" -f $tag, $Name) -ForegroundColor $color }
}

# ---------------------------------------------------------------- 0. 头部
Write-Host ''
Write-Host '==================================================' -ForegroundColor DarkCyan
Write-Host '  小微老师  ·  本机部署启动' -ForegroundColor Cyan
Write-Host "  项目目录: $script:Root" -ForegroundColor DarkGray
Write-Host '==================================================' -ForegroundColor DarkCyan

$logDir = Join-Path $script:Root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
$script:LogPath = Join-Path $logDir ("bootstrap_local_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
try { Start-Transcript -Path $script:LogPath -Force | Out-Null } catch { $script:LogPath = $null }

# ------------------------------------------------------------- 1. Python
function Test-PythonVersion([string]$Exe, [string[]]$PreArgs) {
    if (-not $Exe) { return $null }
    try {
        $out = & $Exe @PreArgs -c "import sys;print('%d.%d.%d'%sys.version_info[:3])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return ($out | Select-Object -First 1).Trim() }
    } catch { }
    return $null
}

function Test-MinVersion([string]$Version, [int]$Major, [int]$Minor) {
    if (-not $Version) { return $false }
    $parts = $Version.Split('.')
    $vmajor = [int]$parts[0]; $vminor = [int]$parts[1]
    if ($vmajor -gt $Major) { return $true }
    if ($vmajor -eq $Major -and $vminor -ge $Minor) { return $true }
    return $false
}

Write-Step '1/5 Python 与虚拟环境'

# 只在最后汇报一次结果，避免"先警告 .venv 不存在、随后又创建成功"这种
# 自相矛盾的两行。
$venvNote = '.venv 不存在'
if (Test-Path $script:VenvPython) {
    $ver = Test-PythonVersion $script:VenvPython @()
    if ($ver -and (Test-MinVersion $ver 3 10)) {
        $script:PyExe = $script:VenvPython
        Add-Check 'OK' '虚拟环境' "已存在，Python $ver"
    } else {
        $venvNote = '存在但不可用，需要重建'
    }
}

if (-not $script:PyExe) {
    if ($NoRepair) {
        Add-Check 'FAIL' '虚拟环境' 'NoRepair 模式，未创建'
    } else {
        $baseExe = $null; $baseArgs = @()
        foreach ($candidate in @('py', 'python')) {
            $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
            if (-not $cmd) { continue }
            $probe = @()
            if ($candidate -eq 'py') { $probe = @('-3.11') }
            $ver = Test-PythonVersion $candidate $probe
            if (-not $ver -and $candidate -eq 'py') { $probe = @('-3'); $ver = Test-PythonVersion $candidate $probe }
            if ($ver -and (Test-MinVersion $ver 3 10)) {
                $baseExe = $candidate; $baseArgs = $probe; break
            }
        }
        if (-not $baseExe) {
            Add-Check 'FAIL' '系统 Python' '未找到 Python 3.10+，请先安装 python.org 版本'
        } else {
            if (Test-Path (Join-Path $script:Root '.venv')) {
                Remove-Item -Recurse -Force (Join-Path $script:Root '.venv') -ErrorAction SilentlyContinue
            }
            Write-Host ("  创建 .venv（{0}）..." -f $venvNote) -ForegroundColor Yellow
            & $baseExe @baseArgs -m venv (Join-Path $script:Root '.venv')
            if (Test-Path $script:VenvPython) {
                $script:PyExe = $script:VenvPython
                Add-Check 'OK' '创建虚拟环境' ("Python " + (Test-PythonVersion $script:PyExe @()))
            } else {
                Add-Check 'FAIL' '创建虚拟环境' 'venv 创建失败'
            }
        }
    }
}

# --------------------------------------------------------- 2. 依赖
Write-Step '2/5 Python 依赖'

if (-not $script:PyExe) {
    Add-Check 'SKIP' '依赖检查' '没有可用 Python'
} else {
    # 依赖清单只有一份，在 deployment\dependencies.py 里：模块名、pip 名、分层、
    # 体积、以及 torch 该装哪个 CUDA 通道，都由它决定。这里只负责调用和汇报。
    $env:PYTHONPATH = $script:Root
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'

    if ($NoRepair) {
        Add-Check 'SKIP' '依赖检查' 'NoRepair 模式，只报告不安装'
        & $script:PyExe -m deployment.dependencies --check | Out-Host
    } else {
        Write-Host '  检查并补齐界面依赖 ...' -ForegroundColor Yellow
        & $script:PyExe -m deployment.dependencies --install --tier core | Out-Host
        if ($LASTEXITCODE -eq 0) {
            Add-Check 'OK' '界面依赖' '已就绪（缺什么补什么）'
        } else {
            Add-Check 'FAIL' '界面依赖' '安装未成功，请检查网络后重试'
        }

        if ($TextOnly) {
            Add-Check 'SKIP' '语音依赖' 'TextOnly 模式，未安装'
        } else {
            Write-Host '  检查并补齐语音依赖（torch 按显卡选 CUDA 通道，体积较大）...' -ForegroundColor Yellow
            & $script:PyExe -m deployment.dependencies --install --tier voice | Out-Host
            if ($LASTEXITCODE -eq 0) {
                Add-Check 'OK' '语音依赖' '已就绪'
            } else {
                Add-Check 'WARN' '语音依赖' '安装未完成，语音功能将不可用'
            }

            & $script:PyExe -m deployment.dependencies --install --tier tts | Out-Host
            if ($LASTEXITCODE -eq 0) {
                Add-Check 'OK' '语音合成依赖' '已就绪'
            } else {
                Add-Check 'WARN' '语音合成依赖' '安装未完成，将只有文字输出'
            }
        }
    }
}


# ------------------------------------------------- 3. 运行目录
Write-Step '3/5 运行目录'
$dirs = @('logs', 'data', 'cache', 'runtime\data', 'media_library\relaxation\temp', 'test_output')
$created = @()
foreach ($d in $dirs) {
    $full = Join-Path $script:Root $d
    if (-not (Test-Path $full)) {
        if ($NoRepair) { continue }
        New-Item -ItemType Directory -Force -Path $full | Out-Null
        $created += $d
    }
}
if ($created.Count -gt 0) { Add-Check 'OK' '补齐目录' ($created -join ', ') }
else { Add-Check 'OK' '运行目录' '齐全' }

# ------------------------------------------------- 4. 模型服务与配置
Write-Step '4/5 模型服务探测与配置生成'

if (-not $script:PyExe) {
    Add-Check 'SKIP' '模型服务' '没有可用 Python'
} elseif ($NoRepair) {
    Add-Check 'SKIP' '模型服务' 'NoRepair 模式，未探测也未启动'
} else {
    # 一次调用完成：探测 → 必要时启动 WSL 模型栈 → 写 config\runtime.local.yaml。
    # 逻辑只有一份，在 deployment\autostart.py 里，应用启动时用的也是它。
    $env:PYTHONPATH = $script:Root
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'
    if ($NoStub) { $env:VOICECHAT_ALLOW_STUB = '0' } else { $env:VOICECHAT_ALLOW_STUB = '1' }
    if ($ProvisionModels) { $env:VOICECHAT_ALLOW_DOWNLOAD = '1' } else { $env:VOICECHAT_ALLOW_DOWNLOAD = '0' }

    # 捕获输出：失败时给一行可读的原因，而不是把 Python traceback 直接甩到屏幕上。
    $autoOut = & $script:PyExe -m deployment.autostart 2>&1
    $autoCode = $LASTEXITCODE
    foreach ($line in $autoOut) { Write-Host ("  " + $line) }
    if ($autoCode -ne 0) {
        $tail = @($autoOut | Where-Object { $_ -match 'Error|错误|Traceback' } | Select-Object -Last 1)
        $detail = 'autostart 返回码 ' + $autoCode
        if ($tail.Count -gt 0) {
            $reason = [string]$tail[0]
            $reason = $reason -replace '^.*?(Error|错误)[:：]\s*', ''
            if ($reason) { $detail = $reason }
        }
        Add-Check 'WARN' '自动配置' $detail
    }

    # 状态用 JSON 读，不解析面向人的中文文本：Windows 控制台代码页和子进程
    # 编码不一致时，抓回来的中文行会变成空串（这里踩过）。
    $statusJson = & $script:PyExe -m deployment.autostart --status --json 2>$null
    $svc = $null
    try { $svc = (($statusJson | Select-Object -Last 1) | ConvertFrom-Json) } catch { }
    if ($svc) {
        foreach ($entry in @(@{ Key = 'dialogue'; Label = '对话' }, @{ Key = 'agent'; Label = 'Agent' })) {
            $one = $svc.($entry.Key)
            if (-not $one) { continue }
            if ($one.ready) {
                Add-Check 'OK' ("服务 :" + $one.port + " " + $entry.Label) (($one.models) -join ',')
            } else {
                Add-Check 'WARN' ("服务 :" + $one.port + " " + $entry.Label) '未就绪'
            }
        }
        if ($svc.wsl_distro) { Add-Check 'OK' 'WSL 发行版' $svc.wsl_distro }
    } else {
        Add-Check 'WARN' '服务状态' '无法解析状态输出'
    }

    if (Test-Path $script:LocalConfig) {
        Add-Check 'OK' '运行配置' 'config\runtime.local.yaml 已生成'
    } else {
        Add-Check 'FAIL' '运行配置' '生成失败'
    }
}

# ------------------------------------------------- 5. 汇总 / 启动
$fails = 0; $warns = 0
Write-Host ''
Write-Host '================ 自检汇总 ================' -ForegroundColor DarkCyan
foreach ($c in $script:Checks) {
    $tag = '[  ]'; $color = 'Gray'
    switch ($c.Status) {
        'OK'   { $tag = '[OK]'; $color = 'Green' }
        'WARN' { $tag = '[!!]'; $color = 'Yellow'; $warns++ }
        'FAIL' { $tag = '[--]'; $color = 'Red'; $fails++ }
        'SKIP' { $tag = '[..]'; $color = 'DarkGray' }
    }
    $line = "  {0} {1}" -f $tag, $c.Name
    if ($c.Detail) { $line += " - $($c.Detail)" }
    Write-Host $line -ForegroundColor $color
}
Write-Host '==========================================' -ForegroundColor DarkCyan
Write-Host ("  通过 {0} 项，警告 {1} 项，失败 {2} 项" -f ($script:Checks.Count - $fails - $warns), $warns, $fails) -ForegroundColor DarkCyan
if ($script:LogPath) { Write-Host "  日志: $script:LogPath" -ForegroundColor DarkGray }
Write-Host ''

if ($CheckOnly) {
    if ($script:LogPath) { try { Stop-Transcript | Out-Null } catch { } }
    if ($fails -gt 0) { exit 1 }
    exit 0
}

if (-not $script:PyExe) {
    Write-Host '环境未就绪，已停止启动。' -ForegroundColor Red
    if ($script:LogPath) { try { Stop-Transcript | Out-Null } catch { } }
    exit 2
}

Write-Step '5/5 启动图形界面'
$configPath = $Config
if (-not $configPath) { $configPath = $script:LocalConfig }
if (-not (Test-Path $configPath)) {
    $configPath = Join-Path $script:Root 'config\runtime.yaml'
    Add-Check 'WARN' '配置回退' "使用 $configPath"
} else {
    $env:VOICECHAT_RUNTIME_CONFIG = (Resolve-Path $configPath).Path
}

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
Set-Location $script:Root

Write-Host "  配置: $configPath" -ForegroundColor DarkGray
Write-Host '>>> 正在打开小微老师...' -ForegroundColor Green
& $script:PyExe 'main.py'
$exitCode = $LASTEXITCODE
Write-Host ''
Write-Host ("程序已退出，返回码 {0}" -f $exitCode) -ForegroundColor DarkGray
if ($script:LogPath) { try { Stop-Transcript | Out-Null } catch { } }
exit $exitCode
