#!/usr/bin/env bash
# Start the two llama.cpp OpenAI-compatible services that voicechat3 expects.
#
#   :8000  dialogue  Qwen3.8-27B-Q4_K_S   (thinking disabled — interview profile)
#   :8001  agent     Qwen3-4B-Instruct-2507
#
# Runs inside WSL2. WSL's localhost forwarding makes both ports reachable from
# Windows as 127.0.0.1:8000 / 127.0.0.1:8001, which is exactly what
# config/runtime.yaml (production) and config/runtime.local.yaml (this machine)
# point at.
#
# Every path is overridable, so the same file works on another workstation:
#   LLAMA_SERVER_BIN   llama-server executable (built with CUDA)
#   DIALOGUE_GGUF      dialogue weights
#   AGENT_GGUF         agent weights
#   DIALOGUE_ALIAS     model id advertised on :8000 (must match the runtime yaml)
#   AGENT_ALIAS        model id advertised on :8001
#
# Usage:
#   bash start_stack_wsl.sh              # start both, wait until ready
#   bash start_stack_wsl.sh --stop       # stop both
#   bash start_stack_wsl.sh --status     # report only
set -uo pipefail

LLAMA_SERVER_BIN="${LLAMA_SERVER_BIN:-/root/llama_cpp_python-0.3.36/vendor/llama.cpp/build/bin/llama-server}"
DIALOGUE_GGUF="${DIALOGUE_GGUF:-/root/models/Qwen3.8-27B-Q4_K_S.gguf}"
AGENT_GGUF="${AGENT_GGUF:-/root/models/Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf}"
DIALOGUE_ALIAS="${DIALOGUE_ALIAS:-Qwen3.8-27B-Q4_K_S}"
AGENT_ALIAS="${AGENT_ALIAS:-Qwen3-4B-Instruct-2507}"
LOG_DIR="${LOG_DIR:-/root}"
# 127.0.0.1 keeps the service private to WSL and relies on WSL's localhost
# forwarding (the normal setup). Set HOST=0.0.0.0 when that forwarding does not
# work on a given machine: the service is then reachable from Windows through
# the WSL NAT address, which is still not routable from the physical LAN.
HOST="${HOST:-127.0.0.1}"

ready() {
    curl -s -m 3 "http://127.0.0.1:$1/v1/models" 2>/dev/null | grep -q '"id"'
}

status() {
    for port in 8000 8001; do
        if ready "$port"; then
            echo "[stack] :$port up   $(curl -s -m 3 "http://127.0.0.1:$port/v1/models" | head -c 160)"
        else
            echo "[stack] :$port down"
        fi
    done
}

stop_all() {
    pkill -f "llama-server.*--port 8000" 2>/dev/null && echo "[stack] stopped :8000" || echo "[stack] :8000 not running"
    pkill -f "llama-server.*--port 8001" 2>/dev/null && echo "[stack] stopped :8001" || echo "[stack] :8001 not running"
}

case "${1:-}" in
    --stop)   stop_all; exit 0 ;;
    --status) status;   exit 0 ;;
esac

if [ ! -x "$LLAMA_SERVER_BIN" ]; then
    echo "[stack] llama-server not executable: $LLAMA_SERVER_BIN" >&2
    echo "[stack] set LLAMA_SERVER_BIN or build llama.cpp first" >&2
    exit 3
fi
for f in "$DIALOGUE_GGUF" "$AGENT_GGUF"; do
    if [ ! -f "$f" ]; then
        echo "[stack] weights missing: $f" >&2
        exit 4
    fi
done

wait_ready() {
    local port=$1 name=$2
    for i in $(seq 1 150); do
        if ready "$port"; then
            echo "[stack] $name ready on :$port after $((i * 2))s"
            return 0
        fi
        sleep 2
    done
    echo "[stack] $name FAILED on :$port" >&2
    return 1
}

# An already-serving port is left alone: llama-server holds tens of GB of VRAM
# and must never be started twice.
if ready 8000; then
    echo "[stack] dialogue already serving on :8000"
else
    echo "[stack] starting dialogue ($DIALOGUE_ALIAS) on :8000 $(date)"
    setsid "$LLAMA_SERVER_BIN" \
        -m "$DIALOGUE_GGUF" \
        --host "$HOST" --port 8000 \
        -c 8192 -ngl 99 -np 1 \
        --alias "$DIALOGUE_ALIAS" \
        --jinja \
        --chat-template-kwargs '{"enable_thinking":false}' \
        --temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.0 \
        --repeat-penalty 1.05 \
        > "$LOG_DIR/llama_8000.log" 2>&1 < /dev/null &
    disown
    wait_ready 8000 dialogue || { tail -40 "$LOG_DIR/llama_8000.log"; exit 1; }
fi

if ready 8001; then
    echo "[stack] agent already serving on :8001"
else
    echo "[stack] starting agent ($AGENT_ALIAS) on :8001 $(date)"
    setsid "$LLAMA_SERVER_BIN" \
        -m "$AGENT_GGUF" \
        --host "$HOST" --port 8001 \
        -c 8192 -ngl 99 -np 2 \
        --alias "$AGENT_ALIAS" \
        --jinja \
        --temp 0.3 --top-p 0.9 --top-k 20 --min-p 0.0 \
        --repeat-penalty 1.05 \
        > "$LOG_DIR/llama_8001.log" 2>&1 < /dev/null &
    disown
    wait_ready 8001 agent || { tail -40 "$LOG_DIR/llama_8001.log"; exit 1; }
fi

echo "[stack] both services up $(date)"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null || true
