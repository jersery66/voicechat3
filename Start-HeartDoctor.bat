@echo off
REM ============================================================
REM  Heart Doctor - one-click launcher
REM
REM  Double-click to: check the environment, repair what is
REM  missing, wait for the inference services, then open the GUI.
REM
REM  Options are forwarded to scripts\launch\bootstrap.ps1:
REM    -CheckOnly     self-check only, do not open the GUI
REM    -NoRepair      report only, change nothing
REM    -FullRepair    also install torch / funasr / transformers
REM    -Deep          extra read-only doctor.py diagnostics
REM
REM  While the GUI runs this window stays open; closing the GUI
REM  returns here and the window closes by itself.
REM ============================================================
chcp 65001 >nul 2>nul
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch\bootstrap.ps1" %*
set "EC=%ERRORLEVEL%"

if not "%EC%"=="0" (
    echo.
    echo Startup failed with code %EC%.
    echo Details: logs\bootstrap_*.log
    pause
)
exit /b %EC%
