<#
.SYNOPSIS
    本机部署启动器：自动适配这台机器的 Python / 依赖 / 模型服务 / 路径。

.DESCRIPTION
    与生产用的 bootstrap.ps1 分开，互不影响：
      bootstrap.ps1        面向实验机（固定 5090 + llama.cpp + 本地权重，strict 预检）
      bootstrap_local.ps1  面向"任意一台机器"（本机没有权重时自动降级到离线桩）

    本脚本按顺序做：
      1. 找 Python 3.10+，必要时建 .venv
      2. 按需补装核心依赖（重型 torch/funasr 只提示，-FullRepair 才装）
      3. 探测本机已有的 /v1 模型服务、ASR/TTS 权重、音色提示
      4. 都没有时，拉起离线桩服务（仅界面/流程联调），再生成运行配置
      5. 用生成的 config\runtime.local.yaml 启动图形界面

    生成的 config\runtime.local.yaml 只是本机的，不入库；
    生产的 config\runtime.yaml 一个字节都不改。

.PARAMETER CheckOnly
    只自检和生成配置，不启动界面。

.PARAMETER NoRepair
    只报告，不改磁盘（不建 venv、不装依赖、不写配置）。

.PARAMETER NoStub
    即使没有模型服务，也不拉起离线桩（此时界面会因连不上模型而拒绝启动）。

.PARAMETER FullRepair
    连 torch / funasr / transformers 一起装（体积大，语音功能需要）。

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
    [switch]$FullRepair,
    [string]$Config = ''
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:VenvPython = Join-Path $script:Root '.venv\Scripts\python.exe'
$script:LocalConfig = Join-Path $script:Root 'config\runtime.local.yaml'
$script:StubPort = 8010
$script:StubPidFile = Join-Path $script:Root 'logs\offline_stub.pid'
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

if (Test-Path $script:VenvPython) {
    $ver = Test-PythonVersion $script:VenvPython @()
    if ($ver -and (Test-MinVersion $ver 3 10)) {
        $script:PyExe = $script:VenvPython
        Add-Check 'OK' '虚拟环境' "已存在，Python $ver"
    } else {
        Add-Check 'WARN' '虚拟环境' '存在但不可用，需要重建'
    }
} else {
    Add-Check 'WARN' '虚拟环境' '.venv 不存在'
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
            Write-Host '  创建 .venv ...' -ForegroundColor Yellow
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

$coreModules = @('PySide6', 'numpy', 'yaml', 'requests', 'sounddevice', 'soundfile',
                 'pygame', 'openai', 'jieba', 'reportlab', 'moviepy', 'pydub', 'scipy', 'PIL')
$heavyModules = @('torch', 'torchaudio', 'funasr', 'transformers')

function Get-MissingModules([string[]]$Names) {
    # Print one missing module per line. The probe is a single-quoted -c
    # one-liner with no spaces, so PowerShell 5.1 cannot mangle the quoting when
    # it hands the string to python.exe.
    if (-not $script:PyExe) { return @() }
    $probeCode = 'import importlib.util,sys;print(chr(10).join([n for n in sys.argv[1:] if importlib.util.find_spec(n) is None]))'
    $raw = & $script:PyExe -c $probeCode @Names 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $raw) { return @() }
    return @($raw | ForEach-Object { ("$_").Trim() } | Where-Object { $_ })
}

function Install-Pip([string[]]$Names) {
    & $script:PyExe -m pip install --disable-pip-version-check --no-input --quiet @Names
    if ($LASTEXITCODE -ne 0) {
        Write-Host '  默认源失败，改用清华镜像重试 ...' -ForegroundColor Yellow
        & $script:PyExe -m pip install --disable-pip-version-check --no-input --quiet `
            -i https://pypi.tuna.tsinghua.edu.cn/simple @Names
    }
    return $LASTEXITCODE -eq 0
}

if (-not $script:PyExe) {
    Add-Check 'SKIP' '依赖检查' '没有可用 Python'
} else {
    $missingCore = @(Get-MissingModules $coreModules)
    if ($missingCore.Count -eq 0) {
        Add-Check 'OK' '核心依赖' '全部就绪'
    } elseif ($NoRepair) {
        Add-Check 'WARN' '核心依赖缺失' (($missingCore -join ', ') + '（NoRepair 未安装）')
    } else {
        $pipNames = @()
        foreach ($m in $missingCore) {
            switch ($m) {
                'PIL'  { $pipNames += 'pillow' }
                'yaml' { $pipNames += 'PyYAML' }
                default { $pipNames += $m }
            }
        }
        Write-Host ("  安装: {0}" -f ($pipNames -join ' ')) -ForegroundColor Yellow
        if ($pipNames.Count -eq 0) {
            Add-Check 'WARN' '核心依赖' '探测结果异常，请手动执行 pip install -r requirements-core.txt'
        } elseif (Install-Pip $pipNames) {
            Add-Check 'OK' '安装核心依赖' (($missingCore -join ', ') + ' 已补齐')
        } else {
            Add-Check 'FAIL' '安装核心依赖' 'pip 失败，请检查网络'
        }
    }

    $missingHeavy = @(Get-MissingModules $heavyModules)
    if ($missingHeavy.Count -eq 0) {
        Add-Check 'OK' '语音 / GPU 依赖' '全部就绪'
    } elseif ($FullRepair -and -not $NoRepair) {
        Write-Host '  安装重型依赖（体积大，含 torch）...' -ForegroundColor Yellow
        if (Install-Pip $missingHeavy) {
            Add-Check 'OK' '语音 / GPU 依赖' '已补齐'
        } else {
            Add-Check 'WARN' '语音 / GPU 依赖' '安装未成功，语音输入输出不可用'
        }
    } else {
        Add-Check 'WARN' '语音 / GPU 依赖缺失' (($missingHeavy -join ', ') + '（文字对话可用；语音请加 -FullRepair）')
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
Write-Step '4/5 模型服务探测'

$autoScript = Join-Path $script:Root 'scripts\launch\autoconfigure.py'
$stubScript = Join-Path $script:Root 'scripts\launch\offline_stub_server.py'

function Get-AutoState {
    $raw = & $script:PyExe $autoScript --json 2>$null
    if (-not $raw) { return $null }
    try { return (($raw | Select-Object -Last 1) | ConvertFrom-Json) } catch { return $null }
}

if (-not $script:PyExe) {
    Add-Check 'SKIP' '模型服务' '没有可用 Python'
} else {
    $state = Get-AutoState
    $realEndpoints = @()
    if ($state) { $realEndpoints = @($state.endpoints | Where-Object { -not $_.is_stub }) }

    if ($realEndpoints.Count -gt 0) {
        foreach ($ep in $realEndpoints) {
            Add-Check 'OK' ("服务 " + $ep.base_url) ($ep.label + " / " + ($ep.models -join ','))
        }
    } else {
        Add-Check 'WARN' '模型服务' '未发现任何 /v1 模型服务'

        # Second chance before degrading to the stub: this machine may carry the
        # production stack inside WSL2 (llama-server + gguf weights).
        $stackReady = $false
        if (-not $NoStub -and -not $NoRepair) {
            $stackScript = Join-Path $script:Root 'scripts\launch\start_model_stack.ps1'
            if (Test-Path $stackScript) {
                Write-Host '  尝试启动 WSL 模型栈（llama.cpp）...' -ForegroundColor Yellow
                & powershell -NoProfile -ExecutionPolicy Bypass -File $stackScript -WaitSeconds 180
                if ($LASTEXITCODE -eq 0) { $stackReady = $true }
            }
        }
        if ($stackReady) {
            $state = Get-AutoState
            if ($state) { $realEndpoints = @($state.endpoints | Where-Object { -not $_.is_stub }) }
            foreach ($ep in $realEndpoints) {
                Add-Check 'OK' ("服务 " + $ep.base_url) ($ep.label + " / " + ($ep.models -join ','))
            }
        }

        if ($realEndpoints.Count -gt 0) {
            # handled above
        } elseif ($NoStub) {
            Add-Check 'WARN' '离线桩' 'NoStub 已指定，未启动（界面将无法连接模型）'
        } elseif ($NoRepair) {
            Add-Check 'SKIP' '离线桩' 'NoRepair 模式，未启动'
        } else {
            $stubUp = $false
            if ($state -and -not $state.stub_port_free) {
                $check = & $script:PyExe $stubScript --check 2>$null
                if ($LASTEXITCODE -eq 0) { $stubUp = $true }
            }
            if (-not $stubUp) {
                Write-Host '  启动离线桩服务（仅界面/流程联调）...' -ForegroundColor Yellow
                $stubOut = Join-Path $logDir 'offline_stub.out.log'
                $proc = Start-Process -FilePath $script:PyExe `
                    -ArgumentList @($stubScript, '--port', "$script:StubPort") `
                    -WindowStyle Hidden -PassThru `
                    -RedirectStandardOutput $stubOut `
                    -RedirectStandardError (Join-Path $logDir 'offline_stub.err.log')
                if ($proc) { Set-Content -Path $script:StubPidFile -Value $proc.Id -Encoding ascii }
                $deadline = (Get-Date).AddSeconds(15)
                while ((Get-Date) -lt $deadline) {
                    & $script:PyExe $stubScript --check 2>$null | Out-Null
                    if ($LASTEXITCODE -eq 0) { $stubUp = $true; break }
                    Start-Sleep -Milliseconds 300
                }
            }
            if ($stubUp) {
                Add-Check 'WARN' '离线桩服务' "http://127.0.0.1:$script:StubPort/v1（非真实推理，仅联调）"
            } else {
                Add-Check 'FAIL' '离线桩服务' '启动失败，见 logs\offline_stub.err.log'
            }
        }
    }

    if (-not $NoRepair) {
        & $script:PyExe $autoScript --quiet 2>$null | Out-Null
        if (Test-Path $script:LocalConfig) {
            Add-Check 'OK' '运行配置' 'config\runtime.local.yaml 已生成'
        } else {
            Add-Check 'FAIL' '运行配置' '生成失败'
        }
    } else {
        Add-Check 'SKIP' '运行配置' 'NoRepair 模式，未生成'
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
