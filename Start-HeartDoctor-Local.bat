@echo off
REM ============================================================
REM  Heart Doctor - local machine launcher
REM
REM  Coexists with the production Start-HeartDoctor.bat:
REM    Start-HeartDoctor.bat        lab machine (fixed models, strict preflight)
REM    Start-HeartDoctor-Local.bat  any machine (auto-detect, degrade instead of dying)
REM
REM  Arguments are forwarded to scripts\launch\bootstrap_local.ps1:
REM    -CheckOnly        self-check and write the config, do not open the GUI
REM    -NoRepair         report only, change nothing
REM    -NoStub           never start the offline stub when no model server answers
REM    -TextOnly         skip torch / funasr / voxcpm (text-only install)
REM    -ProvisionModels  download missing speech weights (FunASR ~2GB, VoxCPM2 ~4.7GB)
REM ============================================================
chcp 65001 >nul 2>nul
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch\bootstrap_local.ps1" %*
set "EC=%ERRORLEVEL%"

if not "%EC%"=="0" (
    echo.
    echo Startup failed with code %EC%. Details: logs\bootstrap_local_*.log
    pause
)
exit /b %EC%
