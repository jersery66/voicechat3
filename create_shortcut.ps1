$WshShell = New-Object -comObject WScript.Shell
$DesktopPath = [System.Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $DesktopPath "语音对话助手.lnk"
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)

# Target: cmd.exe (To run activation script)
$Shortcut.TargetPath = "C:\Windows\System32\cmd.exe"

# Arguments: Activate environment -> Run Python
$Shortcut.Arguments = '/k "call C:\ProgramData\anaconda3\Scripts\activate.bat C:\ProgramData\anaconda3\envs\voice_chat && python d:\program\voice_chat_app\main.py"'

# Start in: App directory
$Shortcut.WorkingDirectory = "d:\program\voice_chat_app"

# Icon: Use python.exe icon
$Shortcut.IconLocation = "C:\ProgramData\anaconda3\envs\voice_chat\python.exe,0"

$Shortcut.Save()

Write-Host "Shortcut created at: $ShortcutPath"
