@echo off
REM Offline patch for machines with no network. Double-click to run.
REM ASCII only and CRLF line endings: cmd.exe misparses UTF-8 Chinese here.
chcp 65001 >nul 2>nul
setlocal
cd /d "%~dp0..\.."

set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================
echo  VoiceChat - offline patch
echo  Program dir : %CD%
echo  Python      : %PY%
echo ============================================
echo.

"%PY%" scripts\launch\offline_patch.py --dry-run
if errorlevel 1 goto trouble

echo.
echo Check finished. No file has been modified yet.
echo Press any key to APPLY the patch, or close this window to cancel.
pause >nul
echo.

"%PY%" scripts\launch\offline_patch.py
echo.
echo Done. Restart the program with Start-HeartDoctor-Local.bat.
echo If the program stops working, restore the backups with:
echo    "%PY%" scripts\launch\offline_patch.py --revert
echo.
pause
exit /b 0

:trouble
echo.
echo Check did not fully pass. Nothing was modified.
echo Please send the output above to support.
pause
exit /b 1
