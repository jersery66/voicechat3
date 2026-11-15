<#
.SYNOPSIS
    小微老师 一键启动：环境自检、自动修复、服务就绪、启动程序。

.DESCRIPTION
    按顺序完成下列步骤，任一步失败都会给出可读原因而不是直接闪退：
      1. 定位 Python，必要时创建 .venv
      2. 检测缺失依赖并自动安装（重型 GPU/ASR 依赖只提示，加 -FullRepair 才装）
      3. 补齐运行目录与 .env
      4. 检查模型、知识库、放松媒体
      5. 检查推理服务，未就绪则尝试启动
      6. 启动图形界面

.PARAMETER Config
    指定 runtime 配置。默认依次尝试：
    VOICECHAT_RUNTIME_CONFIG 环境变量 -> config\runtime_offline_5090_tacit4b.yaml -> config\runtime.yaml

.PARAMETER CheckOnly
    只做自检与修复，不启动图形界面。

.PARAMETER NoRepair
    只报告问题，不修改磁盘（不建 venv、不装依赖、不补目录）。

.PARAMETER FullRepair
    连 torch / torchaudio / funasr / transformers 等重型依赖也自动安装。

.PARAMETER Deep
    额外运行 scripts\deployment\doctor.py 做深度只读诊断（较慢，约数十秒）。

.PARAMETER ServiceWaitSeconds
    等待推理服务就绪的最长秒数，默认 240。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\launch\bootstrap.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\launch\bootstrap.ps1 -CheckOnly
#>
[CmdletBinding()]
param(
    [string]$Config = '',
    [switch]$CheckOnly,
    [switch]$NoRepair,
    [switch]$FullRepair,
    [switch]$Deep,
    [int]$ServiceWaitSeconds = 240
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:VenvDir = Join-Path $script:Root '.venv'
$script:VenvPython = Join-Path $script:VenvDir 'Scripts\python.exe'
$script:Checks = New-Object System.Collections.ArrayList
$script:PyExe = $null
$script:Started = (Get-Date)

function Write-Banner {
    Write-Host ''
    Write-Host '==================================================' -ForegroundColor DarkCyan
    Write-Host '  小微老师  ·  一键启动' -ForegroundColor Cyan
    Write-Host "  项目目录: $script:Root" -ForegroundColor DarkGray
    Write-Host '==================================================' -ForegroundColor DarkCyan
}

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host ">> $Text" -ForegroundColor Cyan
}

function Add-Check([string]$Status, [string]$Name, [string]$Detail = '') {
    $null = $script:Checks.Add([pscustomobject]@{ Status = $Status; Name = $Name; Detail = $Detail })
    $tag = '[  ]'
    $color = 'Gray'
    switch ($Status) {
        'OK'   { $tag = '[OK]'; $color = 'Green' }
        'WARN' { $tag = '[!!]'; $color = 'Yellow' }
        'FAIL' { $tag = '[--]'; $color = 'Red' }
        'SKIP' { $tag = '[..]'; $color = 'DarkGray' }
    }
    if ($Detail) {
        Write-Host ("  {0} {1} - {2}" -f $tag, $Name, $Detail) -ForegroundColor $color
    } else {
        Write-Host ("  {0} {1}" -f $tag, $Name) -ForegroundColor $color
    }
}

function Test-PythonExe([string]$Exe) {
    if (-not $Exe) { return $null }
    if (-not (Test-Path $Exe)) { return $null }
    try {
        $out = & $Exe -c "import sys;print('%d.%d.%d'%sys.version_info[:3])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return ($out | Select-Object -First 1).Trim() }
    } catch { }
    return $null
}

function Test-MinVersion([string]$Version, [int]$Major, [int]$Minor) {
    if (-not $Version) { return $false }
    $parts = $Version.Split('.')
    $vmajor = [int]$parts[0]
    $vminor = [int]$parts[1]
    if ($vmajor -gt $Major) { return $true }
    if ($vmajor -eq $Major -and $vminor -ge $Minor) { return $true }
    return $false
}

function Test-Url([string]$Url, [int]$TimeoutSec = 3) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400)
    } catch {
        return $false
    }
}

function Resolve-ConfigPath {
    $candidates = New-Object System.Collections.ArrayList
    if ($Config) { $null = $candidates.Add($Config) }
    if ($env:VOICECHAT_RUNTIME_CONFIG) { $null = $candidates.Add($env:VOICECHAT_RUNTIME_CONFIG) }
    $null = $candidates.Add('config\runtime_offline_5090_tacit4b.yaml')
    $null = $candidates.Add('config\runtime.yaml')
    foreach ($c in $candidates) {
        $p = $c
        if (-not [System.IO.Path]::IsPathRooted($p)) { $p = Join-Path $script:Root $p }
        if (Test-Path $p) { return (Resolve-Path $p).Path }
    }
    return $null
}

function Get-BaseUrls([string]$ConfigPath) {
    $urls = New-Object System.Collections.ArrayList
    if (-not $ConfigPath) { return $urls }
    foreach ($line in (Get-Content -Path $ConfigPath -Encoding UTF8)) {
        if ($line -match '^\s*base_url\s*:\s*["'']?([^"''#\s]+)') {
            $u = $Matches[1].TrimEnd('/')
            if (-not $urls.Contains($u)) { $null = $urls.Add($u) }
        }
    }
    return $urls
}

# ---------------------------------------------------------------- 1. Python
function Initialize-Python {
    if (Test-Path $script:VenvPython) {
        $ver = Test-PythonExe $script:VenvPython
        if ($ver -and (Test-MinVersion $ver 3 10)) {
            $script:PyExe = $script:VenvPython
            Add-Check 'OK' '虚拟环境' "Python $ver"
            return $true
        }
        Add-Check 'WARN' '虚拟环境不可用' '存在 .venv 但无法执行，尝试重建'
    } else {
        Add-Check 'WARN' '虚拟环境缺失' '.venv 不存在'
    }

    if ($NoRepair) {
        Add-Check 'WARN' '虚拟环境' 'NoRepair 模式，未创建'
        return $false
    }

    $baseExe = $null
    $baseArgs = @()
    foreach ($candidate in @('py', 'python')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $probeArgs = @()
        if ($candidate -eq 'py') { $probeArgs = @('-3') }
        $ver = $null
        try {
            $out = & $candidate @probeArgs -c "import sys;print('%d.%d.%d'%sys.version_info[:3])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) { $ver = ($out | Select-Object -First 1).Trim() }
        } catch { }
        if ($ver -and (Test-MinVersion $ver 3 10)) {
            $baseExe = $candidate
            $baseArgs = $probeArgs
            break
        }
    }

    if (-not $baseExe) {
        Add-Check 'FAIL' '系统 Python' '未找到 Python 3.10+，请先安装 https://www.python.org/downloads/'
        return $false
    }

    if (Test-Path $script:VenvDir) {
        Write-Host '  重建已损坏的 .venv ...' -ForegroundColor Yellow
        Remove-Item -Recurse -Force $script:VenvDir -ErrorAction SilentlyContinue
    }

    Write-Host '  创建 .venv ...' -ForegroundColor Yellow
    & $baseExe @baseArgs -m venv $script:VenvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $script:VenvPython)) {
        Add-Check 'FAIL' '创建虚拟环境' 'venv 创建失败'
        return $false
    }
    $script:PyExe = $script:VenvPython
    $ver = Test-PythonExe $script:VenvPython
    Add-Check 'OK' '创建虚拟环境' "Python $ver"
    return $true
}

# ------------------------------------------------------------ 2. Dependencies
function Repair-Dependencies {
    if (-not $script:PyExe) {
        Add-Check 'SKIP' '依赖检查' '没有可用 Python'
        return
    }

    $probeCode = @'
import importlib.util, json
core = ["PySide6","numpy","yaml","requests","sounddevice","soundfile","pygame","openai","jieba","reportlab","moviepy","pydub","scipy","PIL"]
heavy = ["torch","torchaudio","funasr","transformers"]

def installed(name):
    # find_spec only locates the package; it never executes torch/funasr,
    # which would otherwise add a minute to every launch.
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False

missing_core = [m for m in core if not installed(m)]
missing_heavy = [m for m in heavy if not installed(m)]
print(json.dumps({"core": missing_core, "heavy": missing_heavy}))
'@

    $raw = $null
    $probeFile = Join-Path $env:TEMP ("voicechat_dep_probe_{0}.py" -f ([guid]::NewGuid().ToString('N')))
    try {
        # PowerShell 5.1 strips double quotes when passing a string to a native
        # exe, which corrupts a `-c` one-liner.  A temp script file is safe.
        [System.IO.File]::WriteAllText($probeFile, $probeCode, (New-Object System.Text.UTF8Encoding($false)))
        $raw = & $script:PyExe $probeFile 2>$null
    } catch {
        $raw = $null
    } finally {
        if (Test-Path $probeFile) { Remove-Item $probeFile -Force -ErrorAction SilentlyContinue }
    }
    $result = $null
    try { $result = ($raw | Select-Object -Last 1) | ConvertFrom-Json } catch { }
    if (-not $result) {
        Add-Check 'WARN' '依赖检查' '无法解析检测结果'
        return
    }

    $missingCore = @($result.core)
    $missingHeavy = @($result.heavy)

    if ($missingCore.Count -eq 0) {
        Add-Check 'OK' '核心依赖' '全部就绪'
    } else {
        Add-Check 'WARN' '核心依赖缺失' ($missingCore -join ', ')
        if ($NoRepair) {
            Add-Check 'SKIP' '安装依赖' 'NoRepair 模式'
        } else {
            $pipNames = @()
            foreach ($m in $missingCore) {
                switch ($m) {
                    'PIL' { $pipNames += 'pillow' }
                    'yaml' { $pipNames += 'PyYAML' }
                    default { $pipNames += $m }
                }
            }
            Write-Host ("  安装: {0}" -f ($pipNames -join ' ')) -ForegroundColor Yellow
            & $script:PyExe -m pip install --disable-pip-version-check --no-input @pipNames
            if ($LASTEXITCODE -ne 0) {
                Write-Host '  默认源失败，改用清华镜像重试 ...' -ForegroundColor Yellow
                & $script:PyExe -m pip install --disable-pip-version-check --no-input `
                    -i https://pypi.tuna.tsinghua.edu.cn/simple @pipNames
            }
            if ($LASTEXITCODE -eq 0) {
                Add-Check 'OK' '安装依赖' '核心依赖已补齐'
            } else {
                Add-Check 'FAIL' '安装依赖' 'pip 安装失败，请检查网络或手动执行 pip install -r requirements.txt'
            }
        }
    }

    if ($missingHeavy.Count -eq 0) {
        Add-Check 'OK' '语音 / GPU 依赖' '全部就绪'
    } else {
        $detail = ($missingHeavy -join ', ')
        if ($FullRepair -and -not $NoRepair) {
            Write-Host ("  安装重型依赖（体积较大）: {0}" -f $detail) -ForegroundColor Yellow
            & $script:PyExe -m pip install --disable-pip-version-check --no-input @missingHeavy
            if ($LASTEXITCODE -eq 0) {
                Add-Check 'OK' '语音 / GPU 依赖' '已补齐'
            } else {
                Add-Check 'WARN' '语音 / GPU 依赖' '安装未成功，语音输入输出会不可用'
            }
        } else {
            Add-Check 'WARN' '语音 / GPU 依赖缺失' "$detail（文字对话可用；需要语音请加 -FullRepair）"
        }
    }
}

# ------------------------------------------------------------- 3. 目录/.env
function Repair-Layout {
    $dirs = @('logs', 'test_output', 'data', 'cache', 'media_library\relaxation\temp')
    $created = @()
    foreach ($d in $dirs) {
        $full = Join-Path $script:Root $d
        if (-not (Test-Path $full)) {
            if ($NoRepair) { continue }
            New-Item -ItemType Directory -Force -Path $full | Out-Null
            $created += $d
        }
    }
    if ($created.Count -gt 0) {
        Add-Check 'OK' '补齐目录' ($created -join ', ')
    } else {
        Add-Check 'OK' '运行目录' '齐全'
    }

    $envFile = Join-Path $script:Root '.env'
    $envSample = Join-Path $script:Root '.env.example'
    if (Test-Path $envFile) {
        Add-Check 'OK' '.env' '已存在'
    } elseif ((Test-Path $envSample) -and -not $NoRepair) {
        Copy-Item $envSample $envFile
        Add-Check 'OK' '.env' '由 .env.example 生成，可按需修改'
    } else {
        Add-Check 'WARN' '.env' '缺失，将使用默认配置'
    }
}

# ------------------------------------------------------------- 4. 资源文件
function Test-Resources {
    $kb = Join-Path $script:Root 'knowledge_base\knowledge.json'
    if (Test-Path $kb) {
        $size = [math]::Round((Get-Item $kb).Length / 1KB, 1)
        Add-Check 'OK' '知识库' "knowledge.json ($size KB)"
    } else {
        Add-Check 'FAIL' '知识库' '缺少 knowledge_base\knowledge.json'
    }

    $modelsDir = Join-Path $script:Root 'models'
    if (-not (Test-Path $modelsDir)) {
        Add-Check 'FAIL' '模型目录' '缺少 models\（可为软链接到实际权重目录）'
    } else {
        $gguf = @(Get-ChildItem -Path $modelsDir -Recurse -Filter *.gguf -ErrorAction SilentlyContinue)
        if ($gguf.Count -gt 0) {
            Add-Check 'OK' '本地模型' ("{0} 个 .gguf 权重" -f $gguf.Count)
        } else {
            Add-Check 'WARN' '本地模型' 'models\ 下未找到 .gguf'
        }
    }

    $mediaDir = Join-Path $script:Root 'media_library\relaxation'
    if (Test-Path $mediaDir) {
        $videos = @(Get-ChildItem -Path $mediaDir -Filter *.mp4 -ErrorAction SilentlyContinue)
        if ($videos.Count -gt 0) {
            Add-Check 'OK' '放松媒体' ("{0} 个视频" -f $videos.Count)
        } else {
            Add-Check 'WARN' '放松媒体' '无 mp4，放松中心只能播放派生音频或不可用'
        }
    } else {
        Add-Check 'WARN' '放松媒体' '目录不存在'
    }
}

# --------------------------------------------------------------- 5. 推理服务
function Initialize-Services([string]$ConfigPath) {
    if (-not $ConfigPath) {
        Add-Check 'FAIL' 'runtime 配置' '未找到 config\runtime*.yaml'
        return $false
    }
    $rel = $ConfigPath.Replace($script:Root + '\', '')
    Add-Check 'OK' 'runtime 配置' $rel

    $baseUrls = @(Get-BaseUrls $ConfigPath)
    if ($baseUrls.Count -eq 0) {
        Add-Check 'WARN' '服务端点' '配置中未解析到 base_url'
        return $true
    }

    $downUrls = @()
    foreach ($u in $baseUrls) {
        if (Test-Url "$u/models") {
            Add-Check 'OK' "服务 $u" '已就绪'
        } else {
            Add-Check 'WARN' "服务 $u" '未响应'
            $downUrls += $u
        }
    }

    if ($downUrls.Count -eq 0) { return $true }

    if ($NoRepair) {
        Add-Check 'FAIL' '启动服务' 'NoRepair 模式，未尝试启动（strict_preflight 下 GUI 会拒绝启动）'
        return $false
    }

    $llamaDir = Join-Path $script:Root 'bin\llama'
    $llamaExe = Join-Path $llamaDir 'llama-server.exe'
    if (-not (Test-Path $llamaExe)) {
        Add-Check 'FAIL' '推理运行时' '缺少 bin\llama\llama-server.exe，无法自动拉起服务'
        return $false
    }

    $env:PATH = "$llamaDir;$env:PATH"

    $dialogueBat = Join-Path $script:Root 'scripts\engines\llama_cpp\start_dialogue_only.bat'
    $agentBat = Join-Path $script:Root 'scripts\run_agent.bat'

    $ggufs = @(Get-ChildItem -Path (Join-Path $script:Root 'models\llm') -Filter *.gguf -ErrorAction SilentlyContinue)
    if ($ggufs.Count -gt 0 -and -not $env:VOICECHAT_DIALOGUE_GGUF) {
        $env:VOICECHAT_DIALOGUE_GGUF = $ggufs[0].FullName
    }
    $agentGgufs = @(Get-ChildItem -Path (Join-Path $script:Root 'models\agent') -Filter *.gguf -ErrorAction SilentlyContinue)
    if ($agentGgufs.Count -gt 0 -and -not $env:VOICECHAT_AGENT_GGUF) {
        $env:VOICECHAT_AGENT_GGUF = $agentGgufs[0].FullName
    }

    foreach ($u in $downUrls) {
        $isDialogue = $u -match ':8000'
        if ($isDialogue) {
            if (Test-Path $dialogueBat) {
                Write-Host "  启动对话服务 :8000 ..." -ForegroundColor Yellow
                Start-Process -FilePath $env:ComSpec -ArgumentList '/c', "`"$dialogueBat`"" -WindowStyle Minimized | Out-Null
            } else {
                Add-Check 'FAIL' '对话服务' '缺少 start_dialogue_only.bat'
            }
        } else {
            if (Test-Path $agentBat) {
                Write-Host "  启动 Agent 服务 $u ..." -ForegroundColor Yellow
                Start-Process -FilePath $env:ComSpec -ArgumentList '/c', "`"$agentBat`"" -WindowStyle Minimized | Out-Null
            } else {
                Add-Check 'FAIL' 'Agent 服务' '缺少 scripts\run_agent.bat'
            }
        }
    }

    $deadline = (Get-Date).AddSeconds($ServiceWaitSeconds)
    $lastReport = 0
    while ((Get-Date) -lt $deadline) {
        $stillDown = @()
        foreach ($u in $downUrls) {
            if (-not (Test-Url "$u/models" 2)) { $stillDown += $u }
        }
        if ($stillDown.Count -eq 0) { break }
        $elapsed = [int]((Get-Date) - $script:Started).TotalSeconds
        if (($elapsed - $lastReport) -ge 15) {
            Write-Host ("  等待模型加载... {0}s（最长 {1}s）" -f $elapsed, $ServiceWaitSeconds) -ForegroundColor DarkGray
            $lastReport = $elapsed
        }
        Start-Sleep -Seconds 2
    }

    $failed = @()
    foreach ($u in $downUrls) {
        if (Test-Url "$u/models" 2) {
            Add-Check 'OK' "服务 $u" '已启动并就绪'
        } else {
            Add-Check 'FAIL' "服务 $u" '超时未就绪'
            $failed += $u
        }
    }
    return ($failed.Count -eq 0)
}

# ----------------------------------------------------------------- 6. 启动
function Start-Application([string]$ConfigPath) {
    if (-not $script:PyExe) {
        Add-Check 'FAIL' '启动' '没有可用 Python'
        return 2
    }

    if ($ConfigPath) {
        $env:VOICECHAT_RUNTIME_CONFIG = $ConfigPath
    }
    $env:PYTHONIOENCODING = 'utf-8'
    Set-Location $script:Root

    Add-Check 'OK' '启动' 'python main.py'
    Write-Host ''
    & $script:PyExe 'main.py'
    return $LASTEXITCODE
}

function Write-Summary {
    Write-Host ''
    Write-Host '================ 自检汇总 ================' -ForegroundColor DarkCyan
    $fails = 0
    $warns = 0
    foreach ($c in $script:Checks) {
        $tag = '[  ]'
        $color = 'Gray'
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
    $elapsed = [int]((Get-Date) - $script:Started).TotalSeconds
    Write-Host ('==========================================' ) -ForegroundColor DarkCyan
    Write-Host ("  通过 {0} 项，警告 {1} 项，失败 {2} 项，耗时 {3}s" -f ($script:Checks.Count - $fails - $warns), $warns, $fails, $elapsed) -ForegroundColor DarkCyan
    if ($script:LogPath) {
        Write-Host "  日志: $script:LogPath" -ForegroundColor DarkGray
    }
    Write-Host ''
    return $fails
}

# ================================================================== 主流程
Write-Banner

$logDir = Join-Path $script:Root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
$script:LogPath = Join-Path $logDir ("bootstrap_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
try { Start-Transcript -Path $script:LogPath -Force | Out-Null } catch { $script:LogPath = $null }

$configPath = Resolve-ConfigPath

Write-Step '1/6 Python 与虚拟环境'
$pythonOk = Initialize-Python

Write-Step '2/6 Python 依赖'
if ($pythonOk) {
    Repair-Dependencies
} else {
    Add-Check 'SKIP' '依赖检查' '无可用 Python，已跳过'
}

# Layout and resources do not need Python; keep reporting them so a broken
# machine shows every gap in one pass instead of one at a time.
Write-Step '3/6 运行目录与环境变量'
Repair-Layout

Write-Step '4/6 模型 / 知识库 / 媒体'
Test-Resources

Write-Step '5/6 推理服务'
if ($pythonOk) {
    $servicesOk = Initialize-Services $configPath
} else {
    Add-Check 'SKIP' '推理服务' '环境未就绪，已跳过'
    $servicesOk = $false
}

if ($Deep) {
    Write-Step '附加: 深度诊断'
    if ($script:PyExe -and $configPath) {
        $doctor = Join-Path $script:Root 'scripts\deployment\doctor.py'
        if (Test-Path $doctor) {
            $doctorOut = & $script:PyExe $doctor --config $configPath 2>&1
            $status = 'UNKNOWN'
            foreach ($line in $doctorOut) {
                if ($line -match '"overall_status"\s*:\s*"(\w+)"') { $status = $Matches[1] }
            }
            if ($status -eq 'FAIL') {
                Add-Check 'WARN' '深度诊断' 'doctor 报告 FAIL，见上方输出'
            } else {
                Add-Check 'OK' '深度诊断' "doctor: $status"
            }
        } else {
            Add-Check 'SKIP' '深度诊断' 'doctor.py 不存在'
        }
    }
}

$fails = Write-Summary

if ($CheckOnly) {
    if ($script:LogPath) { try { Stop-Transcript | Out-Null } catch { } }
    if ($fails -gt 0) { exit 1 }
    exit 0
}

if (-not $pythonOk) {
    Write-Host '环境未就绪，已停止启动。' -ForegroundColor Red
    if ($script:LogPath) { try { Stop-Transcript | Out-Null } catch { } }
    exit 2
}

if (-not $servicesOk) {
    Write-Host '推理服务未就绪。严格预检模式下 GUI 会拒绝启动，已停止。' -ForegroundColor Red
    Write-Host '可先手动检查 :8000 / :8001，或查看上方失败项。' -ForegroundColor Yellow
    if ($script:LogPath) { try { Stop-Transcript | Out-Null } catch { } }
    exit 3
}

Write-Step '6/6 启动图形界面'
Write-Host ''
Write-Host '>>> 一切就绪，正在打开小微老师...' -ForegroundColor Green
$exitCode = Start-Application $configPath
Write-Host ''
Write-Host ("程序已退出，返回码 {0}" -f $exitCode) -ForegroundColor DarkGray
if ($script:LogPath) { try { Stop-Transcript | Out-Null } catch { } }
exit $exitCode
