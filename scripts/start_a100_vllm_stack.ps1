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

Start-VoiceChatVllmService -Model "Qwen/Qwen2.5-72B-Instruct-AWQ" -Port 8000 -GpuMemoryUtilization 0.82 -MaxModelLen 8192
Start-VoiceChatVllmService -Model "Qwen/Qwen2.5-3B-Instruct-AWQ" -Port 8001 -GpuMemoryUtilization 0.08 -MaxModelLen 4096

$deadline = (Get-Date).AddMinutes(8)
foreach ($port in 8000, 8001) {
    do {
        try {
            $null = Invoke-RestMethod -Uri "http://127.0.0.1:$port/v1/models" -TimeoutSec 5 -ErrorAction Stop
            break
        } catch {
            Start-Sleep -Seconds 2
        }
    } while ((Get-Date) -lt $deadline)

    if ((Get-Date) -ge $deadline) {
        throw "vLLM service on port $port did not become ready within 8 minutes."
    }
}

Set-Location $ProjectRoot
& $Python main.py
