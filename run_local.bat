@echo off
REM VoiceChat 本地启动（便捷入口）
REM 双击即可运行，内部调用 run_local.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_local.ps1"
if errorlevel 1 (
    echo.
    echo 启动失败，请检查上方错误信息。
    pause
)
