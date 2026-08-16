#!/usr/bin/env bash
set -euo pipefail

service_name=""
runtime_dir="${VOICECHAT_VLLM_RUNTIME_DIR:-$HOME/.voicechat/vllm}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --service-name)
            [[ $# -ge 2 ]] || { echo "--service-name requires a value" >&2; exit 2; }
            service_name="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

[[ "$service_name" =~ ^[A-Za-z0-9_-]+$ ]] || {
    echo "Invalid service name: $service_name" >&2
    exit 2
}

pid_file="$runtime_dir/$service_name.pid"
if [[ ! -f "$pid_file" ]]; then
    echo "No pid file for $service_name; nothing to stop."
    exit 0
fi

pid="$(cat "$pid_file" 2>/dev/null || true)"
if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    rm -f "$pid_file"
    echo "Removed stale pid file for $service_name."
    exit 0
fi

if kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 30); do
        if ! kill -0 "$pid" 2>/dev/null; then
            break
        fi
        sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
        kill -KILL "$pid" 2>/dev/null || true
    fi
fi

rm -f "$pid_file"
echo "Stopped $service_name service (pid $pid)."
