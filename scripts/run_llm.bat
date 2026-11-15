@echo off
REM voicechat3 - main dialogue model on :8000 (llama.cpp CUDA)
setlocal
set LLAMA=D:\voicechat3\bin\llama
set MODEL=D:\models\voicechat3\llm\Qwen3.8-27B-Q4_K_S.gguf

REM reuse CUDA runtime shipped with torch (cudart/cublas already in bin\llama)
set PATH=D:\voicechat3\bin\llama;%PATH%

"%LLAMA%\llama-server.exe" ^
  -m "%MODEL%" ^
  -c 16384 ^
  -ngl 999 ^
  -fa auto ^
  --host 127.0.0.1 ^
  --port 8000 ^
  -a qwen3.8-27b ^
  --metrics
endlocal
