[CmdletBinding()]
param(
    [string]$Distro = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

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

try {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        throw "wsl.exe is not available on PATH."
    }
    $projectWslRootResult = Invoke-WslCapture -Arguments @(
        "wslpath", "-a", "-u", $ProjectRoot
    )
    if ($projectWslRootResult.ExitCode -ne 0) {
        throw "Could not map the project root into WSL: $($projectWslRootResult.Output)"
    }
    $projectWslRoot = $projectWslRootResult.Output.Trim()
    $stopScript = "$projectWslRoot/scripts/wsl/stop_vllm_service.sh"
    $overallExitCode = 0
    foreach ($serviceName in @("dialogue", "agent")) {
        $result = Invoke-WslCapture -Arguments @(
            "bash", $stopScript, "--service-name", $serviceName
        )
        if (-not [string]::IsNullOrWhiteSpace($result.Output)) {
            Write-Host $result.Output
        }
        if ($result.ExitCode -ne 0) {
            $overallExitCode = 1
        }
    }
    exit $overallExitCode
} catch {
    Write-Error $_
    exit 1
}
