@echo off
REM 离线机器一键补丁：双击运行即可。
REM 先检查（不改任何文件），确认后再应用；应用前会自动备份。
chcp 65001 >nul 2>nul
setlocal
cd /d "%~dp0..\.."

set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================
echo  小微老师 - 离线补丁
echo  程序目录: %CD%
echo  使用 Python: %PY%
echo ============================================
echo.

"%PY%" scripts\launch\offline_patch.py --dry-run
if errorlevel 1 goto :trouble

echo.
echo 以上为检查结果（还没有修改任何文件）。
echo 按任意键开始应用补丁，或直接关闭窗口放弃。
pause >nul
echo.

"%PY%" scripts\launch\offline_patch.py
echo.
echo 完成。请重新双击启动器运行程序。
echo 若程序无法启动，运行本目录下的还原命令：
echo    "%PY%" scripts\launch\offline_patch.py --revert
echo.
pause
exit /b 0

:trouble
echo.
echo 检查未全部通过，已停止，未修改任何文件。
echo 请把上面的输出截图发给技术支持。
pause
exit /b 1
