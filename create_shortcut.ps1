# Create Desktop Shortcut Script
# Creates a VoiceChat3 desktop shortcut with keyboard shortcut Ctrl+Alt+V

# Desktop path
$desktopPath = [Environment]::GetFolderPath("Desktop")

# Shortcut path
$shortcutPath = "$desktopPath\VoiceChat3 - AI Counseling.lnk"

# Target path (startup script)
$targetPath = "d:\program\voicechat0.3\voicechat3\start_voicechat3.bat"

# Working directory
$workingDirectory = "d:\program\voicechat0.3\voicechat3"

# Icon path (use default if not found)
$iconPath = "d:\program\voicechat0.3\voicechat3\ui\background.jpg"

# Create WScript.Shell object
$WshShell = New-Object -ComObject WScript.Shell

# Create shortcut
$shortcut = $WshShell.CreateShortcut($shortcutPath)

# Set shortcut properties
$shortcut.TargetPath = $targetPath
$shortcut.WorkingDirectory = $workingDirectory
$shortcut.Description = "VoiceChat3 - AI Counseling Voice System"
$shortcut.WindowStyle = 1  # 1=Normal, 3=Maximized, 7=Minimized

# Set keyboard shortcut: Ctrl+Alt+V
$shortcut.Hotkey = "Ctrl+Alt+V"

# Try to set icon (if file exists)
if (Test-Path $iconPath) {
    $shortcut.IconLocation = "$iconPath,0"
} else {
    # Use default icon
    $shortcut.IconLocation = "shell32.dll,1"
}

# Save shortcut
$shortcut.Save()

Write-Host "Desktop shortcut created successfully!" -ForegroundColor Green
Write-Host "Location: $shortcutPath" -ForegroundColor Yellow
Write-Host "Keyboard shortcut: Ctrl+Alt+V" -ForegroundColor Cyan
Write-Host "Double-click the shortcut or press Ctrl+Alt+V to start VoiceChat3" -ForegroundColor Green