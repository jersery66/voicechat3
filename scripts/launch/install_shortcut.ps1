<#
.SYNOPSIS
    在桌面创建「小微老师」一键启动快捷方式。

.DESCRIPTION
    快捷方式指向项目根目录的 Start-HeartDoctor.bat，工作目录即项目目录，
    因此换机器、换盘符后重建一次即可，不写死绝对路径。
    图标使用随仓库提供的 scripts\launch\xiaowei.ico（红心箭头，背景透明）；
    该文件缺失时退化为现场绘制的绿色圆底「心」字图标。

.PARAMETER Name
    快捷方式显示名，默认「小微老师」。

.PARAMETER Hotkey
    全局快捷键，默认 Ctrl+Alt+H；传空字符串表示不设置。

.PARAMETER Remove
    删除桌面上的同名快捷方式。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\launch\install_shortcut.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\launch\install_shortcut.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$Name = '小微老师',
    [string]$Hotkey = 'Ctrl+Alt+H',
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Target = Join-Path $Root 'Start-HeartDoctor.bat'
$IconPath = Join-Path $PSScriptRoot 'xiaowei.ico'
$Desktop = [Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $Desktop ("{0}.lnk" -f $Name)
$LegacyShortcuts = @('心医生.lnk') | ForEach-Object { Join-Path $Desktop $_ }

if ($Remove) {
    if (Test-Path $ShortcutPath) {
        Remove-Item $ShortcutPath -Force
        Write-Host "[OK] 已删除桌面快捷方式: $ShortcutPath" -ForegroundColor Green
    } else {
        Write-Host "[..] 桌面没有名为「$Name」的快捷方式" -ForegroundColor DarkGray
    }
    exit 0
}

if (-not (Test-Path $Target)) {
    Write-Host "[--] 找不到启动器: $Target" -ForegroundColor Red
    exit 1
}

function New-HeartIcon([string]$Path) {
    try {
        Add-Type -AssemblyName System.Drawing
    } catch {
        return $false
    }
    try {
        $size = 256
        $bmp = New-Object System.Drawing.Bitmap($size, $size)
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
        $g.Clear([System.Drawing.Color]::Transparent)

        $brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 46, 125, 91))
        $g.FillEllipse($brush, 6, 6, $size - 12, $size - 12)

        $font = New-Object System.Drawing.Font('Microsoft YaHei', 132, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
        $fmt = New-Object System.Drawing.StringFormat
        $fmt.Alignment = [System.Drawing.StringAlignment]::Center
        $fmt.LineAlignment = [System.Drawing.StringAlignment]::Center
        $rect = New-Object System.Drawing.RectangleF(0, 0, $size, $size)
        $g.DrawString('心', $font, [System.Drawing.Brushes]::White, $rect, $fmt)
        $g.Dispose()

        $icon = [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
        $stream = [System.IO.File]::Create($Path)
        $icon.Save($stream)
        $stream.Close()
        $bmp.Dispose()
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-Path $IconPath)) {
    $made = New-HeartIcon $IconPath
    if ($made) {
        Write-Host "[OK] 生成图标: $IconPath" -ForegroundColor Green
    } else {
        Write-Host "[!!] 图标生成失败，将使用系统默认图标" -ForegroundColor Yellow
    }
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $Target
$shortcut.WorkingDirectory = $Root
$shortcut.Description = '小微老师 - 一键启动（自检 / 修复 / 启动）'
$shortcut.WindowStyle = 1
if ($Hotkey) { $shortcut.Hotkey = $Hotkey }
if (Test-Path $IconPath) {
    $shortcut.IconLocation = "$IconPath,0"
} else {
    $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,3"
}
$shortcut.Save()

# 旧版本桌面快捷方式名为「心医生」，改名后会被同名逻辑漏掉，这里一并清掉。
foreach ($legacy in $LegacyShortcuts) {
    if ((Test-Path $legacy) -and ($legacy -ne $ShortcutPath)) {
        Remove-Item $legacy -Force
        Write-Host "[OK] 已移除旧快捷方式: $legacy" -ForegroundColor Green
    }
}

Write-Host ''
Write-Host '[OK] 桌面快捷方式已创建' -ForegroundColor Green
Write-Host "     位置: $ShortcutPath" -ForegroundColor Gray
Write-Host "     目标: $Target" -ForegroundColor Gray
if ($Hotkey) { Write-Host "     快捷键: $Hotkey" -ForegroundColor Gray }
Write-Host ''
Write-Host '双击图标即可：自动检查环境 -> 修复缺失项 -> 等待模型服务 -> 打开程序' -ForegroundColor Cyan
