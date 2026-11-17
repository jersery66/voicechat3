<#
.SYNOPSIS
    停止本机部署启动器拉起的后台进程（离线桩服务）。

.DESCRIPTION
    只结束它自己写进 logs\offline_stub.pid 的那个进程；端口上若是别的程序
    在服务，只报告、不杀。图形界面退出后桩服务会留着，方便下次秒开；
    需要彻底清理时运行本脚本。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$pidFile = Join-Path $root 'logs\offline_stub.pid'
$port = 8010

$stopped = $false
if (Test-Path $pidFile) {
    $stubPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($stubPid) {
        $proc = Get-Process -Id ([int]$stubPid) -ErrorAction SilentlyContinue
        if ($proc) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            Write-Host ("  已停止离线桩进程 PID {0}" -f $proc.Id) -ForegroundColor Green
            $stopped = $true
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

if (-not $stopped) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        Write-Host ("  端口 {0} 仍被 PID {1} 占用（不是本启动器拉起的进程，未处理）" -f $port, $conn.OwningProcess) -ForegroundColor Yellow
    } else {
        Write-Host '  没有需要停止的后台服务。' -ForegroundColor DarkGray
    }
}
