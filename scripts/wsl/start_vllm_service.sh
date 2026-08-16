#!/usr/bin/env bash
set -euo pipefail

model=""
port=""
gpu_memory_utilization=""
max_model_len=""
service_name=""
vllm_executable="~/.venvs/voicechat-vllm/bin/vllm"
check_executable=0

usage() {
    echo "Usage: $0 --model MODEL --port PORT --gpu-memory-utilization VALUE --max-model-len N --service-name NAME [--vllm-executable PATH]" >&2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --model)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            model="$2"
            shift 2
            ;;
        --port)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            port="$2"
            shift 2
            ;;
        --gpu-memory-utilization)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            gpu_memory_utilization="$2"
            shift 2
            ;;
        --max-model-len)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            max_model_len="$2"
            shift 2
            ;;
        --service-name)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            service_name="$2"
            shift 2
            ;;
        --vllm-executable)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            vllm_executable="$2"
            shift 2
            ;;
        --check-executable)
            check_executable=1
            shift
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage
            exit 2
            ;;
    esac
done

if [[ "$vllm_executable" == "~/"* ]]; then
    vllm_executable="$HOME/${vllm_executable#~/}"
fi

if [[ "$vllm_executable" == */* ]]; then
    [[ -x "$vllm_executable" ]] || {
        echo "vLLM executable is not executable: $vllm_executable" >&2
        exit 1
    }
else
    vllm_executable="$(command -v "$vllm_executable" || true)"
    [[ -n "$vllm_executable" ]] || {
        echo "vLLM executable was not found on PATH" >&2
        exit 1
    }
fi

if [[ "$check_executable" -eq 1 ]]; then
    exit 0
fi

[[ -n "$model" && -n "$port" && -n "$gpu_memory_utilization" && -n "$max_model_len" && -n "$service_name" ]] || {
    usage
    exit 2
}
[[ "$service_name" =~ ^[A-Za-z0-9_-]+$ ]] || {
    echo "Invalid service name: $service_name" >&2
    exit 2
}
[[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1 && port <= 65535 )) || {
    echo "Invalid port: $port" >&2
    exit 2
}
awk -v value="$gpu_memory_utilization" 'BEGIN { exit !(value > 0 && value < 1) }' || {
    echo "GPU memory utilization must satisfy 0 < value < 1" >&2
    exit 2
}
[[ "$max_model_len" =~ ^[0-9]+$ ]] && (( max_model_len > 0 )) || {
    echo "Invalid max model length: $max_model_len" >&2
    exit 2
}

runtime_dir="${VOICECHAT_VLLM_RUNTIME_DIR:-$HOME/.voicechat/vllm}"
mkdir -p "$runtime_dir"
pid_file="$runtime_dir/$service_name.pid"
log_file="$runtime_dir/$service_name.log"

if [[ -f "$pid_file" ]]; then
    existing_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ "$existing_pid" =~ ^[0-9]+$ ]] && kill -0 "$existing_pid" 2>/dev/null; then
        echo "$service_name service is already running with pid $existing_pid"
        exit 0
    fi
    rm -f "$pid_file"
fi

nohup bash -c 'exec "$@"' _ \
    "$vllm_executable" serve "$model" \
    --host 127.0.0.1 \
    --port "$port" \
    --dtype auto \
    --gpu-memory-utilization "$gpu_memory_utilization" \
    --max-model-len "$max_model_len" \
    --max-num-seqs 4 \
    --enable-prefix-caching \
    >"$log_file" 2>&1 < /dev/null &
pid=$!
echo "$pid" > "$pid_file"
echo "Started $service_name service with pid $pid; log=$log_file"
