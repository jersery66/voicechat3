@echo off
chcp 65001 >nul 2>nul
cd /d "d:\program\voicechat0.3\voicechat3"

echo ========================================
echo   VoiceChat3 - AI Psychological System
echo ========================================
echo.

echo Starting VoiceChat3...
echo.

"%ProgramData%\anaconda3\envs\voicechat3\python.exe" main.py

echo.
echo Program exited with code: %errorlevel%
pause
