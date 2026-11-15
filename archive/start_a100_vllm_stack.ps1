<#
Start the single-A100 production stack and then launch the desktop client.

The fixed two-service budget is 0.82 (72B dialogue) + 0.08 (3B Agent) = 0.90.
vLLM servers listen only on loopback. Run this script on the A100 host from a
PowerShell environment with ``vllm`` on PATH and the desktop venv installed.
#>

[CmdletBinding()]
param(
    [string]$Python = (Join-Path (Split-Path -Parent $PSScriptRoot) ".venv\Scripts\python.exe")
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SingleServerLauncher = Join-Path $PSScriptRoot "start_vllm_a100.ps1"

if (-not (Test-Path $Python)) {
    throw "Desktop Python was not found: $Python"
}

$env:VOICECHAT_DEPLOYMENT_PROFILE = "a100_80g"
$env:NO_PROXY = "localhost,127.0.0.1"
Remove-Item Env:OLLAMA_MODEL -ErrorAction SilentlyContinue
Remove-Item Env:AGENT_MODEL -ErrorAction SilentlyContinue
Remove-Item Env:VOICECHAT_DIALOGUE_MODEL -ErrorAction SilentlyContinue
Remove-Item Env:VOICECHAT_VLLM_MODEL -ErrorAction SilentlyContinue

function Start-VoiceChatVllmService {
    param(
        [string]$Model,
        [int]$Port,
        [double]$GpuMemoryUtilization,
        [int]$MaxModelLen
    )

    $existing = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "vLLM port $Port is already listening; reusing it." -ForegroundColor Yellow
        return
    }

    Start-Process powershell -WindowStyle Hidden -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $SingleServerLauncher,
        "-Model", $Model,
        "-Port", $Port,
        "-GpuMemoryUtilization", $GpuMemoryUtilization,
        "-MaxModelLen", $MaxModelLen
    )
}

function Wait-VoiceChatVllmModel {
    param(
        [int]$Port,
        [string]$ExpectedModel
    )

    $deadline = (Get-Date).AddMinutes(8)
    do {
        try {
            $models = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/models" -TimeoutSec 5 -ErrorAction Stop
            $modelIds = @($models.data | ForEach-Object { $_.id })
            if ($ExpectedModel -in $modelIds) {
                return
            }
            throw "Expected model '$ExpectedModel' was not served on port $Port. Found: $($modelIds -join ', ')"
        } catch {
            $lastError = $_
            Start-Sleep -Seconds 2
        }
    } while ((Get-Date) -lt $deadline)

    throw "vLLM service on port $Port did not serve '$ExpectedModel' within 8 minutes: $lastError"
}

Start-VoiceChatVllmService -Model "Qwen/Qwen2.5-72B-Instruct-AWQ" -Port 8000 -GpuMemoryUtilization 0.82 -MaxModelLen 8192
Start-VoiceChatVllmService -Model "Qwen/Qwen2.5-3B-Instruct-AWQ" -Port 8001 -GpuMemoryUtilization 0.08 -MaxModelLen 4096

Wait-VoiceChatVllmModel -Port 8000 -ExpectedModel "Qwen/Qwen2.5-72B-Instruct-AWQ"
Wait-VoiceChatVllmModel -Port 8001 -ExpectedModel "Qwen/Qwen2.5-3B-Instruct-AWQ"

& $Python (Join-Path $ProjectRoot "scripts\check_config.py")
if ($LASTEXITCODE -ne 0) {
    throw "A100 production readiness check failed; desktop application was not started."
}

Set-Location $ProjectRoot
& $Python main.py
