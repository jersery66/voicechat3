#!/usr/bin/env bash
# Start ONLY the dialogue llama-server (:8000, Qwen3.8-27B-Q4_K_S) for the
# offline Jev demo.  The legacy agent (:8001) is deliberately NOT started in
# Jev mode: the offline runtime config uses deterministic escalation, so no
# Qwen3-4B weights need to occupy VRAM.
#
# Ownership-safe: if something is already listening on :8000 it is left alone
# and reported, never killed.
#
# IMPORTANT: this script runs llama-server in the FOREGROUND.  A backgrounded
# WSL process dies with its session (WSL reclaims the VM when the last client
# exits); keeping llama-server attached to a live wsl.exe is what keeps the
# VM alive.  The Windows launcher runs this script in a hidden wsl.exe window.
set -u

BIN="${VOICECHAT_LLAMA_SERVER:-/root/llama_cpp_python-0.3.36/vendor/llama.cpp/build/bin/llama-server}"
MODEL="${VOICECHAT_DIALOGUE_GGUF:-/root/models/Qwen3.8-27B-Q4_K_S.gguf}"
PORT="${VOICECHAT_DIALOGUE_PORT:-8000}"
ALIAS="${VOICECHAT_DIALOGUE_ALIAS:-Qwen3.8-27B-Q4_K_S}"

if ! [[ -x "$BIN" ]]; then
    echo "[offline] FAIL: llama-server not executable at $BIN" >&2
    exit 3
fi
if ! [[ -f "$MODEL" ]]; then
    echo "[offline] FAIL: dialogue GGUF not found at $MODEL" >&2
    exit 3
fi

if curl -s -m 3 "http://127.0.0.1:$PORT/v1/models" 2>/dev/null | grep -q '"id"'; then
    echo "[offline] dialogue service already up on :$PORT; nothing to do"
    exit 0
fi

echo "[offline] dialogue llama-server starting on :$PORT (foreground, ~17GB to load)"
exec "$BIN" \
    -m "$MODEL" \
    --host 127.0.0.1 --port "$PORT" \
    -c 8192 -ngl 99 -np 1 \
    --alias "$ALIAS" \
    --jinja \
    --chat-template-kwargs '{"enable_thinking":false}' \
    --temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.0 \
    --repeat-penalty 1.05
