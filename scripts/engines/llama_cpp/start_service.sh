#!/usr/bin/env bash
set -euo pipefail

model=""
port=""
alias_name=""
context_size=""
gpu_layers=""
parallel=""
chat_template_kwargs=""
service_name=""
server_executable="llama-server"
check_executable=0

usage() {
    echo "Usage: $0 --model MODEL --port PORT --alias NAME --context-size N --gpu-layers N --parallel N --service-name NAME [--chat-template-kwargs JSON] [--server-executable PATH]" >&2
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
        --alias)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            alias_name="$2"
            shift 2
            ;;
        --context-size)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            context_size="$2"
            shift 2
            ;;
        --gpu-layers)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            gpu_layers="$2"
            shift 2
            ;;
        --parallel)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            parallel="$2"
            shift 2
            ;;
        --chat-template-kwargs)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            chat_template_kwargs="$2"
            shift 2
            ;;
        --service-name)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            service_name="$2"
            shift 2
            ;;
        --server-executable)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            server_executable="$2"
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

if [[ "$server_executable" == "~/"* ]]; then
    server_executable="$HOME/${server_executable#~/}"
fi

if [[ "$server_executable" == */* ]]; then
    [[ -x "$server_executable" ]] || {
        echo "llama-server executable is not executable: $server_executable" >&2
        exit 1
    }
else
    server_executable="$(command -v "$server_executable" || true)"
    [[ -n "$server_executable" ]] || {
        echo "llama-server executable was not found on PATH" >&2
        exit 1
    }
fi

if [[ "$check_executable" -eq 1 ]]; then
    exit 0
fi

[[ -n "$model" && -n "$port" && -n "$alias_name" && -n "$context_size" && -n "$gpu_layers" && -n "$parallel" && -n "$service_name" ]] || {
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
[[ -n "$alias_name" && "$alias_name" != *[[:space:]]* ]] || {
    echo "Invalid alias: $alias_name" >&2
    exit 2
}
[[ "$context_size" =~ ^[0-9]+$ ]] && (( context_size > 0 )) || {
    echo "Invalid context size: $context_size" >&2
    exit 2
}
[[ "$gpu_layers" =~ ^[0-9]+$ ]] || {
    echo "Invalid gpu layers: $gpu_layers" >&2
    exit 2
}
[[ "$parallel" =~ ^[0-9]+$ ]] && (( parallel >= 1 )) || {
    echo "Invalid parallel slot count: $parallel" >&2
    exit 2
}

# shellcheck source=/dev/null
source "$(dirname "$0")/service_identity.sh"
set_service_context "$service_name"
inspect_service_slot
case "$service_state" in
    SERVICE_RUNNING)
        read_service_metadata || {
            echo "Cannot prove ownership of existing $service_name process; refusing to reuse it." >&2
            exit 1
        }
        if [[ "$metadata_model" != "$model" || "$metadata_port" != "$port" ]]; then
            echo "Owned $service_name process does not match requested model/port; refusing to replace it." >&2
            exit 1
        fi
        echo "$service_name service is already running with pid $service_pid"
        exit 0
        ;;
    OWNERSHIP_MISMATCH)
        echo "Ownership mismatch for $service_name; refusing to kill or replace the live process." >&2
        exit 1
        ;;
    STALE_PID)
        echo "Removing stale $service_name pid metadata before start." >&2
        remove_service_metadata
        ;;
    NO_PID_FILE)
        if service_port_is_listening "$port"; then
            echo "Port $port is occupied without owned metadata; refusing to start an unknown owner." >&2
            exit 1
        else
            port_check_status=$?
            if (( port_check_status == 2 )); then
                echo "Cannot inspect port $port because ss is unavailable; refusing to start without ownership evidence." >&2
                exit 1
            fi
        fi
        ;;
    *)
        echo "Unknown service state: $service_state" >&2
        exit 1
        ;;
esac

runtime_dir="${VOICECHAT_LLAMA_CPP_RUNTIME_DIR:-$HOME/.voicechat/llama_cpp}"
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

server_args=(
    "$server_executable"
    -m "$model"
    --host 127.0.0.1
    --port "$port"
    -c "$context_size"
    -ngl "$gpu_layers"
    -np "$parallel"
    --alias "$alias_name"
    --jinja
)
if [[ -n "$chat_template_kwargs" ]]; then
    server_args+=(--chat-template-kwargs "$chat_template_kwargs")
fi

nohup bash -c 'exec "$@"' _ "${server_args[@]}" \
    >"$log_file" 2>&1 < /dev/null &
pid=$!
metadata_tmp="$(mktemp "$runtime_dir/$service_name.meta.tmp.XXXXXX")"
cat > "$metadata_tmp" <<EOF
service_name=$service_name
pid=$pid
model=$model
port=$port
server_executable=$server_executable
EOF
mv -f -- "$metadata_tmp" "$runtime_dir/$service_name.meta"
printf '%s\n' "$pid" > "$pid_file"
echo "Started $service_name service with pid $pid; log=$log_file"
