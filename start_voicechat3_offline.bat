@echo off
REM ============================================================
REM voicechat3 离线演示启动入口（RTX 5090 / Jev / Tacit 进程内）
REM
REM 用法：
REM   start_voicechat3_offline.bat                 正常启动（含预检 + GUI）
REM   start_voicechat3_offline.bat -PreflightOnly  只做预检不启动 GUI
REM   start_voicechat3_offline.bat -SkipLlmStart   不启动 :8000（已手动启动时）
REM
REM 主模型服务（WSL 内 llama-server :8000）在一个独立的最小化窗口中常驻运行，
%%REM 关闭该窗口或 wsl --shutdown 都会停掉主模型；再次运行本脚本会自动重拉。%%
REM 完整说明见 docs/deployment/offline_run_guide.md
REM ============================================================
if /i "%~1"=="-SkipLlmStart" goto :llm_skipped
start "voicechat3 dialogue (llama-server :8000, 请勿关闭)" /min wsl.exe -e bash /mnt/d/戒毒所/voicechat3/scripts/engines/llama_cpp/start_dialogue_only.sh
:llm_skipped
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\deployment\start_offline.ps1" %*
