@echo off
REM ============================================================
REM voicechat3 dialogue service (:8000) - native Windows launcher
REM
REM Windows port of scripts/engines/llama_cpp/start_dialogue_only.sh
REM (the original runs llama-server inside WSL on the 5090 demo box).
REM
REM Same contract as the shell version:
REM   - foreground llama-server, so the window owning it stays alive
REM   - ownership-safe: if :8000 already serves /v1/models, exit 0
REM   - no model download, no silent provider substitution
REM
REM Overrides (environment):
REM   VOICECHAT_LLAMA_SERVER   path to llama-server.exe
REM   VOICECHAT_DIALOGUE_GGUF  path to the dialogue GGUF
REM   VOICECHAT_DIALOGUE_PORT  default 8000
REM   VOICECHAT_DIALOGUE_ALIAS default Qwen3.8-27B-Q4_K_S
REM ============================================================
setlocal EnableExtensions
set "ROOT=%~dp0..\..\.."
pushd "%ROOT%" 2>nul || (echo [offline] FAIL: cannot enter %ROOT% & pause & exit /b 3)

if not defined VOICECHAT_LLAMA_SERVER set "VOICECHAT_LLAMA_SERVER=%CD%\bin\llama\llama-server.exe"
if not defined VOICECHAT_DIALOGUE_GGUF set "VOICECHAT_DIALOGUE_GGUF=D:\models\voicechat3\llm\Qwen3.8-27B-Q4_K_S.gguf"
if not defined VOICECHAT_DIALOGUE_PORT set "VOICECHAT_DIALOGUE_PORT=8000"
if not defined VOICECHAT_DIALOGUE_ALIAS set "VOICECHAT_DIALOGUE_ALIAS=Qwen3.8-27B-Q4_K_S"

set "BIN=%VOICECHAT_LLAMA_SERVER%"
set "MODEL=%VOICECHAT_DIALOGUE_GGUF%"

if not exist "%BIN%" (
    echo [offline] FAIL: llama-server not found at "%BIN%"
    popd & pause & exit /b 3
)
if not exist "%MODEL%" (
    echo [offline] FAIL: dialogue GGUF not found at "%MODEL%"
    popd & pause & exit /b 3
)

REM --- already serving? leave the running process alone ---------------
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%VOICECHAT_DIALOGUE_PORT%/v1/models' -TimeoutSec 3 -UseBasicParsing; if ($r.Content -match '\"id\"') { exit 0 } else { exit 1 } } catch { exit 1 }"
if not errorlevel 1 (
    echo [offline] dialogue service already up on :%VOICECHAT_DIALOGUE_PORT%; nothing to do
    popd & endlocal & exit /b 0
)

echo [offline] dialogue llama-server starting on :%VOICECHAT_DIALOGUE_PORT% (foreground, ~16GB to load)
set "PATH=%CD%\bin\llama;%PATH%"

REM torch ships the CUDA 13 runtime; bin\llama carries cudart64_13/cublas64_13 copies.
"%BIN%" -m "%MODEL%" ^
    --host 127.0.0.1 --port %VOICECHAT_DIALOGUE_PORT% ^
    -c 8192 -ngl 99 -np 1 -fa auto ^
    --alias "%VOICECHAT_DIALOGUE_ALIAS%" ^
    --jinja --chat-template-kwargs "{\"enable_thinking\":false}" ^
    --temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.0 --repeat-penalty 1.05

set "EC=%ERRORLEVEL%"
echo.
echo [offline] dialogue service exited with code %EC% (window kept open so the error stays readable).
popd
pause
exit /b %EC%
