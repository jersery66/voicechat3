$WshShell = New-Object -comObject WScript.Shell
$DesktopPath = [System.Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $DesktopPath "语音对话助手.lnk"
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)

# Determine script directory (where this ps1 and main.py reside)
$ScriptDir = $PSScriptRoot

# Target: cmd.exe (To run activation script)
$Shortcut.TargetPath = "C:\Windows\System32\cmd.exe"

# Arguments: Activate environment -> Run Python
# Note: Anaconda path is still hardcoded as it depends on system install
$Shortcut.Arguments = '/k "call C:\ProgramData\anaconda3\Scripts\activate.bat C:\ProgramData\anaconda3\envs\voice_chat && python "' + $ScriptDir + '\main.py"'

# Start in: App directory
$Shortcut.WorkingDirectory = $ScriptDir

# Icon: Use python.exe icon
$Shortcut.IconLocation = "C:\ProgramData\anaconda3\envs\voice_chat\python.exe,0"

$Shortcut.Save()

Write-Host "Shortcut created at: $ShortcutPath"
