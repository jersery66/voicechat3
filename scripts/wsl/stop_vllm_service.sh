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

# shellcheck source=/dev/null
source "$(dirname "$0")/vllm_service_identity.sh"
# The helper resolves the owned $runtime_dir/$service_name.pid and matching
# metadata file before any signal is permitted.
set_service_context "$service_name"
inspect_service_slot

if [[ "$service_state" == "NO_PID_FILE" ]]; then
    echo "No pid file for $service_name; nothing to stop."
    exit 0
fi

if [[ "$service_state" == "STALE_PID" ]]; then
    remove_service_metadata
    echo "Removed stale pid metadata for $service_name."
    exit 0
fi

if [[ "$service_state" != "SERVICE_RUNNING" ]]; then
    echo "Ownership mismatch for $service_name; refusing to kill pid $service_pid." >&2
    exit 1
fi

pid="$service_pid"
if ! process_matches_metadata "$pid"; then
    echo "Process identity changed before stopping $service_name; refusing SIGTERM." >&2
    exit 1
fi
kill -TERM "$pid" 2>/dev/null || true
for _ in $(seq 1 30); do
    if ! kill -0 "$pid" 2>/dev/null; then
        break
    fi
    sleep 1
done
if kill -0 "$pid" 2>/dev/null; then
    # Re-check command identity immediately before escalation.  PID reuse must
    # never turn a timeout into a kill of an unrelated process.
    if process_matches_metadata "$pid"; then
        kill -KILL "$pid" 2>/dev/null || true
    else
        echo "Process identity changed while stopping $service_name; refusing SIGKILL." >&2
        exit 1
    fi
fi

if kill -0 "$pid" 2>/dev/null; then
    echo "Could not stop owned $service_name process pid $pid." >&2
    exit 1
fi

remove_service_metadata
echo "Stopped $service_name service (pid $pid)."
