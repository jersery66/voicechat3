@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"

echo ========================================
echo   VoiceChat3 - AI Psychological System
echo ========================================
echo.

echo Starting VoiceChat3...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_local.ps1"

echo.
echo Program exited with code: %errorlevel%
pause
