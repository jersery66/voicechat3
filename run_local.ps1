# ============================================================
# VoiceChat 本地启动脚本（RTX 3060 6GB 适配版）
# ------------------------------------------------------------
# 用法：在项目目录执行  powershell -ExecutionPolicy Bypass -File .\run_local.ps1
#
# 本脚本针对 6GB 显存机器，使用 qwen2.5:7b 作为主对话模型，
# qwen2.5:3b 作为 Agent 路由模型（替代默认的 8b 以省显存）。
# ============================================================

$ErrorActionPreference = "Stop"

# ---------- 路径配置 ----------
$VenvPython = "C:\Users\Jersery\.qoderwork\workspace\msnk4887lp0t9ndj\vcenv\Scripts\python.exe"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$OllamaExe  = "C:\Users\Jersery\AppData\Local\Programs\Ollama\ollama.exe"

# ---------- 模型与数据配置 ----------
# 显式指定模型，避免 config.py 自动检测选错（7b/3b 不在优先列表里）
$env:OLLAMA_MODEL      = "qwen2.5:7b"      # 主对话模型
$env:AGENT_MODEL       = "qwen2.5:3b"      # Agent 路由模型
$env:OLLAMA_HOST       = "http://localhost:11434"
$env:OLLAMA_MODELS     = "E:\ollama_models" # 模型仓库放 E 盘（C 盘空间紧张）
# $env:VOICECHAT_DATA_DIR = "D:\program\voice_chat_data"  # 默认即可，如需改数据目录取消注释
# $env:VOICECHAT_ENGINE_SHADOW = "1"        # 影子模式默认开启

# ---------- 代理配置 ----------
# 本机用户环境变量里有 HTTP_PROXY/HTTPS_PROXY(=127.0.0.1:7890 Clash)。
# NO_PROXY 必须设置：否则 Python SDK 连 localhost:11434 都会走代理，Clash 没开时应用直接瘫痪。
$env:NO_PROXY = "localhost,127.0.0.1"
$proxyAlive = $false
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $iar = $tcp.BeginConnect('127.0.0.1', 7890, $null, $null)
    $proxyAlive = $iar.AsyncWaitHandle.WaitOne(800) -and $tcp.Connected
    $tcp.Close()
} catch { }
if ($proxyAlive) {
    # 拉取/更新模型走代理（直连 Ollama CDN 只有 ~60KB/s，走代理 ~2MB/s）
    $env:HTTP_PROXY  = "http://127.0.0.1:7890"
    $env:HTTPS_PROXY = "http://127.0.0.1:7890"
    $ProxyStatus = "http://127.0.0.1:7890 (Clash 已启用)"
} else {
    $ProxyStatus = "未检测到 Clash(7890)，模型拉取可能很慢"
}

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " VoiceChat 本地启动" -ForegroundColor Cyan
Write-Host " 主模型:   $env:OLLAMA_MODEL" -ForegroundColor Green
Write-Host " Agent:    $env:AGENT_MODEL" -ForegroundColor Green
Write-Host " Ollama:   $env:OLLAMA_HOST" -ForegroundColor Green
Write-Host " 模型仓库: $env:OLLAMA_MODELS" -ForegroundColor Green
Write-Host " 代理:     $ProxyStatus" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Cyan

# ---------- 前置检查 ----------
if (-not (Test-Path $VenvPython)) {
    Write-Host "[错误] 找不到 venv Python: $VenvPython" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $ProjectDir "main.py"))) {
    Write-Host "[错误] 找不到 main.py，请确认脚本位于项目根目录" -ForegroundColor Red
    exit 1
}

# ---------- 确保 Ollama 服务在运行 ----------
$ollamaUp = $false
try {
    $resp = Invoke-RestMethod -Uri "$env:OLLAMA_HOST/api/version" -TimeoutSec 3 -ErrorAction Stop
    $ollamaUp = $true
    Write-Host "[OK] Ollama 服务已运行 (v$($resp.version))" -ForegroundColor Green
} catch {
    Write-Host "[提示] Ollama 服务未运行，尝试启动..." -ForegroundColor Yellow
}

if (-not $ollamaUp) {
    if (-not (Test-Path $OllamaExe)) {
        Write-Host "[错误] 找不到 Ollama: $OllamaExe" -ForegroundColor Red
        exit 1
    }
    New-Item -ItemType Directory -Force -Path $env:OLLAMA_MODELS | Out-Null
    Start-Process -FilePath $OllamaExe -ArgumentList "serve" -WindowStyle Hidden
    Write-Host "[提示] 已启动 Ollama serve，等待就绪..." -ForegroundColor Yellow
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try {
            $null = Invoke-RestMethod -Uri "$env:OLLAMA_HOST/api/version" -TimeoutSec 2 -ErrorAction Stop
            $ready = $true
            break
        } catch { }
    }
    if (-not $ready) {
        Write-Host "[错误] Ollama 服务 30 秒内未就绪" -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Ollama 服务已就绪" -ForegroundColor Green
}

# ---------- 检查所需模型是否已拉取 ----------
$tags = (Invoke-RestMethod -Uri "$env:OLLAMA_HOST/api/tags" -TimeoutSec 5).models
$modelNames = @()
if ($tags) { $modelNames = @($tags | ForEach-Object { $_.model }) }

foreach ($needed in @($env:OLLAMA_MODEL, $env:AGENT_MODEL)) {
    $found = $modelNames | Where-Object { $_ -like "$needed*" }
    if (-not $found) {
        Write-Host "[警告] 模型 $needed 尚未拉取。请先运行: ollama pull $needed" -ForegroundColor Yellow
    } else {
        Write-Host "[OK] 模型已就绪: $found" -ForegroundColor Green
    }
}

# ---------- 启动应用 ----------
Write-Host ""
Write-Host ">>> 启动 VoiceChat ..." -ForegroundColor Cyan
Set-Location $ProjectDir
& $VenvPython main.py
