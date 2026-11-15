@echo off
REM voicechat3 - agent model on :8001 (llama.cpp CUDA)
setlocal
set LLAMA=D:\voicechat3\bin\llama
set MODEL=D:\models\voicechat3\agent\Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf

set PATH=D:\voicechat3\bin\llama;%PATH%

"%LLAMA%\llama-server.exe" ^
  -m "%MODEL%" ^
  -c 8192 ^
  -ngl 999 ^
  -fa auto ^
  --host 127.0.0.1 ^
  --port 8001 ^
  -a qwen3-4b-instruct-2507 ^
  --metrics
endlocal
