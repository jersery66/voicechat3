@echo off
chcp 65001 >nul 2>nul
echo ========================================
echo   VoiceChat3 - AI Psychological System
echo ========================================
echo.

cd /d "d:\program\voicechat0.3\voicechat3"

echo Checking conda environment...
conda env list | findstr /C:"voicechat3" >nul 2>nul
if errorlevel 1 (
    echo ERROR: voicechat3 conda environment not found.
    echo Please create it first: conda create -n voicechat3 python=3.10
    pause
    exit /b 1
)

echo Starting VoiceChat3...
conda run -n voicechat3 --no-capture-output python main.py

if errorlevel 1 (
    echo.
    echo Program exited with error code: %errorlevel%
    echo Check log files for details.
    pause
)