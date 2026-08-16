[CmdletBinding()]
param(
    [ValidateSet("rtxpro6000_96g", "rtxpro6000_96g_qwen38_candidate")]
    [string]$Profile = "rtxpro6000_96g",
    [string]$Python = "",
    [string]$Distro = "",
    [string]$VllmVenv = "",
    [string]$VllmExecutable = "",
    [Parameter(Mandatory = $false)]
    [double]$DialogueGpuMemoryUtilization = 0,
    [Parameter(Mandatory = $false)]
    [double]$AgentGpuMemoryUtilization = 0,
    [Alias("Status")]
    [switch]$VerifyOnly,
    [ValidateRange(1, 262144)]
    [int]$DialogueMaxModelLen = 8192,
    [ValidateRange(1, 262144)]
    [int]$AgentMaxModelLen = 4096,
    [ValidateRange(1, 120)]
    [int]$StartupTimeoutMinutes = 20
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
}
if ([string]::IsNullOrWhiteSpace($VllmExecutable)) {
    if ([string]::IsNullOrWhiteSpace($VllmVenv)) {
        $VllmExecutable = "~/.venvs/voicechat-vllm/bin/vllm"
    } else {
        $VllmExecutable = "$($VllmVenv.TrimEnd('/'))/bin/vllm"
    }
}

$StartupTimeoutSeconds = $StartupTimeoutMinutes * 60
$DialoguePort = 0
$AgentPort = 0
$startedServices = [System.Collections.Generic.List[string]]::new()

function Invoke-WslCapture {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $output = if ([string]::IsNullOrWhiteSpace($Distro)) {
        & wsl.exe -- @Arguments 2>&1
    } else {
        & wsl.exe -d $Distro -- @Arguments 2>&1
    }
    [pscustomobject]@{
        ExitCode = $LASTEXITCODE
        Output = (($output | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine)
    }
}

function Invoke-WslChecked {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $result = Invoke-WslCapture -Arguments $Arguments
    if ($result.ExitCode -ne 0) {
        throw "WSL command failed (exit $($result.ExitCode)): $($result.Output)"
    }
    return $result.Output
}

function Get-ProfileContract {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Windows Python interpreter not found: $Python"
    }
    $profileCode = @'
import json
import sys
from deployment.profiles import get_deployment_profile

p = get_deployment_profile(sys.argv[1])
print(json.dumps({
    "name": p.name,
    "expected_gpu_memory_gb": p.expected_gpu_memory_gb,
    "runtime_backend": p.runtime_backend,
    "dialogue_model": p.dialogue_model,
    "dialogue_base_url": p.dialogue_base_url,
    "router_model": p.router_model,
    "agent_model": p.agent_model,
    "agent_base_url": p.agent_base_url,
    "immutable_runtime_contract": p.immutable_runtime_contract,
    "strict_preflight": p.strict_preflight,
}, ensure_ascii=False))
'@
    $raw = & $Python -c $profileCode $Profile 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to resolve deployment profile '$Profile': $($raw -join ' ')"
    }
    try {
        return (($raw -join [Environment]::NewLine) | ConvertFrom-Json)
    } catch {
        throw "Deployment profile export was not valid JSON: $($raw -join ' ')"
    }
}

function Test-ProfileContract {
    param([Parameter(Mandatory = $true)]$Contract)

    if ($Contract.runtime_backend -ne "vllm") {
        throw "Profile '$($Contract.name)' is not a vLLM profile."
    }
    if (-not $Contract.immutable_runtime_contract) {
        throw "Profile '$($Contract.name)' does not declare an immutable runtime contract."
    }
    if (-not $Contract.strict_preflight) {
        throw "Profile '$($Contract.name)' is not strict-preflight enabled."
    }
    if ([int]$Contract.expected_gpu_memory_gb -ne 96) {
        throw "Profile '$($Contract.name)' is not a 96GB Blackwell target profile."
    }

    $dialogueUri = [Uri]$Contract.dialogue_base_url
    $agentUri = [Uri]$Contract.agent_base_url
    if ($dialogueUri.Host -ne "127.0.0.1" -or $dialogueUri.Port -ne 8000) {
        throw "Profile dialogue endpoint must remain 127.0.0.1:8000."
    }
    if ($agentUri.Host -ne "127.0.0.1" -or $agentUri.Port -ne 8001) {
        throw "Profile Agent endpoint must remain 127.0.0.1:8001."
    }
    $script:DialoguePort = $dialogueUri.Port
    $script:AgentPort = $agentUri.Port
}

function Test-WslPrerequisites {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        throw "wsl.exe is not available on PATH."
    }
    $uname = (Invoke-WslChecked -Arguments @("uname", "-s")).Trim()
    if ($uname -ne "Linux") {
        throw "Selected WSL distribution did not report Linux (got '$uname')."
    }
    [void](Invoke-WslChecked -Arguments @("nvidia-smi"))
    $script:ProjectWslRoot = (Invoke-WslChecked -Arguments @(
        "wslpath", "-a", "-u", $ProjectRoot
    )).Trim()
    if ([string]::IsNullOrWhiteSpace($ProjectWslRoot)) {
        throw "Could not map the project root into WSL."
    }
    $script:WslStartScript = "$ProjectWslRoot/scripts/wsl/start_vllm_service.sh"
    $script:WslStopScript = "$ProjectWslRoot/scripts/wsl/stop_vllm_service.sh"
    $script:WslStatusScript = "$ProjectWslRoot/scripts/wsl/status_vllm_service.sh"
    $script:WslIdentityHelper = "$ProjectWslRoot/scripts/wsl/vllm_service_identity.sh"
    # These repository scripts are invoked explicitly through bash. A
    # Windows-mounted path may not present the executable bit, so only require
    # regular files here; the vLLM executable check remains strict below.
    [void](Invoke-WslChecked -Arguments @("test", "-f", $WslStartScript))
    [void](Invoke-WslChecked -Arguments @("test", "-f", $WslStopScript))
    [void](Invoke-WslChecked -Arguments @("test", "-f", $WslStatusScript))
    [void](Invoke-WslChecked -Arguments @("test", "-f", $WslIdentityHelper))
    $checkResult = Invoke-WslCapture -Arguments @(
        "bash", $WslStartScript, "--check-executable", "--vllm-executable", $VllmExecutable
    )
    if ($checkResult.ExitCode -ne 0) {
        throw "WSL vLLM executable is unavailable: $($checkResult.Output)"
    }
}

function Test-MemoryBudget {
    if ($VerifyOnly) {
        return
    }
    if ($DialogueGpuMemoryUtilization -le 0 -or $DialogueGpuMemoryUtilization -ge 1) {
        throw "DialogueGpuMemoryUtilization must satisfy 0 < value < 1."
    }
    if ($AgentGpuMemoryUtilization -le 0 -or $AgentGpuMemoryUtilization -ge 1) {
        throw "AgentGpuMemoryUtilization must satisfy 0 < value < 1."
    }
    if (($DialogueGpuMemoryUtilization + $AgentGpuMemoryUtilization) -ge 1) {
        throw "Dialogue and Agent GPU utilization values must sum to less than 1."
    }
}

function Test-TcpPort {
    param([Parameter(Mandatory = $true)][int]$Port)

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync("127.0.0.1", $Port)
        if (-not $task.Wait(1000)) {
            return $false
        }
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Get-EndpointProbe {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [string]$ExpectedModel = ""
    )

    try {
        $payload = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/models" -TimeoutSec 5
        $ids = @($payload.data | ForEach-Object { $_.id } | Where-Object { $_ })
        $status = if (-not [string]::IsNullOrWhiteSpace($ExpectedModel) -and $ids -notcontains $ExpectedModel) {
            "ENDPOINT WRONG MODEL"
        } else {
            "ENDPOINT HEALTHY"
        }
        return [pscustomobject]@{
            Status = $status
            Responding = $true
            Models = $ids
            Detail = if ($status -eq "ENDPOINT WRONG MODEL") {
                "Expected '$ExpectedModel'; server exposed '$($ids -join ', ')'"
            } else {
                "HTTP /v1/models responded"
            }
        }
    } catch {
        if (Test-TcpPort -Port $Port) {
            return [pscustomobject]@{
                Status = "PORT LISTENING"
                Responding = $false
                Models = @()
                Detail = "TCP listener present but /v1/models did not respond"
            }
        }
        return [pscustomobject]@{
            Status = "PORT FREE"
            Responding = $false
            Models = @()
            Detail = "No loopback listener detected"
        }
    }
}

function Get-WslServiceStatus {
    param([Parameter(Mandatory = $true)][ValidatePattern("^[A-Za-z0-9_-]+$")][string]$ServiceName)

    $result = Invoke-WslCapture -Arguments @("bash", $WslStatusScript, "--service-name", $ServiceName)
    if ($result.ExitCode -ne 0) {
        throw "Could not inspect WSL $ServiceName ownership: $($result.Output)"
    }
    $values = @{}
    foreach ($line in ($result.Output -split "`r?`n")) {
        if ($line -match "^(?<key>[^=]+)=(?<value>.*)$") {
            $values[$Matches.key] = $Matches.value
        }
    }
    return [pscustomobject]$values
}

function Test-WslServiceRunning {
    param([Parameter(Mandatory = $true)][ValidatePattern("^[A-Za-z0-9_-]+$")][string]$ServiceName)

    $status = Get-WslServiceStatus -ServiceName $ServiceName
    return $status.state -eq "SERVICE_RUNNING"
}

function Start-WslService {
    param(
        [Parameter(Mandatory = $true)][string]$ServiceName,
        [Parameter(Mandatory = $true)][string]$Model,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][double]$GpuMemoryUtilization,
        [Parameter(Mandatory = $true)][int]$MaxModelLen
    )

    $result = Invoke-WslCapture -Arguments @(
        "bash", $WslStartScript,
        "--model", $Model,
        "--port", "$Port",
        "--gpu-memory-utilization", "$GpuMemoryUtilization",
        "--max-model-len", "$MaxModelLen",
        "--service-name", $ServiceName,
        "--vllm-executable", $VllmExecutable
    )
    if ($result.ExitCode -ne 0) {
        throw "Could not start WSL $ServiceName service: $($result.Output)"
    }
    if (-not [string]::IsNullOrWhiteSpace($result.Output)) {
        Write-Host $result.Output
    }
}

function Wait-ForExactModel {
    param(
        [Parameter(Mandatory = $true)][string]$ServiceName,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$ExpectedModel
    )

    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $probe = Get-EndpointProbe -Port $Port
        if ($probe.Responding) {
            if ($probe.Models -contains $ExpectedModel) {
                Write-Host "$ServiceName :800$([int]($Port - 8000)) is ready with exact model '$ExpectedModel'."
                return
            }
            $seen = ($probe.Models -join ", ")
            throw "Wrong model on 127.0.0.1:$Port; expected '$ExpectedModel', saw '$seen'."
        }
        if (-not (Test-WslServiceRunning -ServiceName $ServiceName)) {
            throw "$ServiceName service is not running. Inspect ~/.voicechat/vllm/$ServiceName.log in WSL."
        }
        Write-Host "Waiting for $ServiceName model readiness (bounded timeout ${StartupTimeoutMinutes}m)..."
        Start-Sleep -Seconds 2
    }
    throw "Timed out waiting for $ServiceName exact model '$ExpectedModel'. Inspect ~/.voicechat/vllm/$ServiceName.log in WSL."
}

function Ensure-Service {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("agent", "dialogue")][string]$ServiceName,
        [Parameter(Mandatory = $true)][string]$ExpectedModel,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][double]$GpuMemoryUtilization,
        [Parameter(Mandatory = $true)][int]$MaxModelLen
    )

    $probe = Get-EndpointProbe -Port $Port
    if ($probe.Responding) {
        if ($probe.Models -contains $ExpectedModel) {
            Write-Host "Reusing pre-existing $ServiceName service with exact model '$ExpectedModel'."
            return
        }
        throw "Port $Port is occupied by a different model; refusing to kill or replace it."
    }
    if ($probe.Status -eq "PORT LISTENING") {
        throw "Port $Port is occupied by an unknown listener; refusing to kill or replace it."
    }

    $serviceStatus = Get-WslServiceStatus -ServiceName $ServiceName
    if ($serviceStatus.state -eq "OWNERSHIP_MISMATCH") {
        throw "Ownership mismatch for $ServiceName; refusing to replace its live PID."
    }
    if ($serviceStatus.state -ne "SERVICE_RUNNING") {
        Start-WslService -ServiceName $ServiceName -Model $ExpectedModel -Port $Port `
            -GpuMemoryUtilization $GpuMemoryUtilization -MaxModelLen $MaxModelLen
        [void]$startedServices.Add($ServiceName)
    } else {
        Write-Host "Found pre-existing $ServiceName WSL process; waiting without taking ownership."
    }
    Wait-ForExactModel -ServiceName $ServiceName -Port $Port -ExpectedModel $ExpectedModel
}

function Stop-ServiceByName {
    param([Parameter(Mandatory = $true)][ValidateSet("agent", "dialogue")][string]$ServiceName)

    $result = Invoke-WslCapture -Arguments @("bash", $WslStopScript, "--service-name", $ServiceName)
    if ($result.ExitCode -ne 0) {
        Write-Warning "Could not stop newly started $ServiceName service: $($result.Output)"
    }
}

function Clear-MisleadingOverrides {
    $env:VOICECHAT_DEPLOYMENT_PROFILE = $Profile
    $env:NO_PROXY = "localhost,127.0.0.1"
    foreach ($name in @(
        "OLLAMA_MODEL",
        "AGENT_MODEL",
        "VOICECHAT_DIALOGUE_MODEL",
        "VOICECHAT_VLLM_MODEL",
        "VOICECHAT_DIALOGUE_BASE_URL",
        "VOICECHAT_AGENT_BASE_URL"
    )) {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
}

function Invoke-Verify {
    param([Parameter(Mandatory = $true)]$Contract)

    $agentOwnership = Get-WslServiceStatus -ServiceName "agent"
    $dialogueOwnership = Get-WslServiceStatus -ServiceName "dialogue"
    $agentEndpoint = Get-EndpointProbe -Port $AgentPort -ExpectedModel $Contract.agent_model
    $dialogueEndpoint = Get-EndpointProbe -Port $DialoguePort -ExpectedModel $Contract.dialogue_model
    $report = [pscustomobject]@{
        action = "VERIFY"
        profile = $Contract.name
        expected_agent_model = $Contract.agent_model
        expected_dialogue_model = $Contract.dialogue_model
        agent_port = $AgentPort
        dialogue_port = $DialoguePort
        agent_endpoint_status = $agentEndpoint.Status
        dialogue_endpoint_status = $dialogueEndpoint.Status
        agent_model_ids = $agentEndpoint.Models
        dialogue_model_ids = $dialogueEndpoint.Models
        agent_pid_state = $agentOwnership.state
        dialogue_pid_state = $dialogueOwnership.state
        agent_pid = $agentOwnership.pid
        dialogue_pid = $dialogueOwnership.pid
        agent_pid_detail = $agentOwnership.detail
        dialogue_pid_detail = $dialogueOwnership.detail
        gui = "NOT STARTED"
        service_lifecycle_owner = "Windows launcher + WSL PID metadata"
        manifest_source_of_truth = "DeploymentProfile"
    }
    $report | ConvertTo-Json -Depth 8
    if ($agentEndpoint.Status -eq "ENDPOINT HEALTHY" -and $dialogueEndpoint.Status -eq "ENDPOINT HEALTHY") {
        return 0
    }
    return 1
}

$exitCode = 0
$applicationStarted = $false
Push-Location $ProjectRoot
try {
    $contract = Get-ProfileContract
    Test-ProfileContract -Contract $contract
    Test-WslPrerequisites
    if ($VerifyOnly) {
        $exitCode = Invoke-Verify -Contract $contract
    } else {
        Test-MemoryBudget
        Clear-MisleadingOverrides

        Ensure-Service -ServiceName "agent" -ExpectedModel $contract.agent_model -Port $AgentPort `
            -GpuMemoryUtilization $AgentGpuMemoryUtilization -MaxModelLen $AgentMaxModelLen
        Ensure-Service -ServiceName "dialogue" -ExpectedModel $contract.dialogue_model -Port $DialoguePort `
            -GpuMemoryUtilization $DialogueGpuMemoryUtilization -MaxModelLen $DialogueMaxModelLen

        $checkScript = Join-Path $ProjectRoot "scripts\check_config.py"
        & $Python $checkScript
        $checkExit = $LASTEXITCODE
        if ($checkExit -ne 0) {
            throw "Strict configuration preflight failed with exit code $checkExit."
        }

        Write-Host "Preflight passed; launching Windows PySide6 application."
        $applicationStarted = $true
        & $Python (Join-Path $ProjectRoot "main.py")
        $exitCode = $LASTEXITCODE
    }
} catch {
    Write-Error $_
    for ($index = $startedServices.Count - 1; $index -ge 0; $index--) {
        Stop-ServiceByName -ServiceName $startedServices[$index]
    }
    $exitCode = 1
} finally {
    Pop-Location
}

exit $exitCode
