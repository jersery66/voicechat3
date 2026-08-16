#!/usr/bin/env bash

# Shared, side-effect-free process identity helpers for the vLLM service
# scripts.  The caller owns lifecycle actions; these helpers only inspect the
# service slot and /proc command line before a lifecycle action is permitted.

set_service_context() {
    local name="$1"
    [[ "$name" =~ ^[A-Za-z0-9_-]+$ ]] || {
        echo "Invalid service name: $name" >&2
        return 2
    }
    runtime_dir="${VOICECHAT_VLLM_RUNTIME_DIR:-$HOME/.voicechat/vllm}"
    pid_file="$runtime_dir/$name.pid"
    metadata_file="$runtime_dir/$name.meta"
    log_file="$runtime_dir/$name.log"
    service_name="$name"
}

read_service_metadata() {
    metadata_service_name=""
    metadata_pid=""
    metadata_model=""
    metadata_port=""
    metadata_vllm_executable=""
    [[ -f "$metadata_file" ]] || return 1
    local key value
    while IFS='=' read -r key value; do
        case "$key" in
            service_name) metadata_service_name="$value" ;;
            pid) metadata_pid="$value" ;;
            model) metadata_model="$value" ;;
            port) metadata_port="$value" ;;
            vllm_executable) metadata_vllm_executable="$value" ;;
        esac
    done < "$metadata_file"
    [[ "$metadata_service_name" == "$service_name" ]] || return 1
    [[ "$metadata_pid" =~ ^[0-9]+$ ]] || return 1
    [[ -n "$metadata_model" && "$metadata_port" =~ ^[0-9]+$ ]] || return 1
}

process_command_line() {
    local pid="$1"
    [[ -r "/proc/$pid/cmdline" ]] || return 1
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null
}

process_matches_metadata() {
    local pid="$1"
    local command_line
    command_line="$(process_command_line "$pid" || true)"
    [[ -n "$command_line" ]] || return 1
    [[ "$command_line" == *" serve "* ]] || return 1
    [[ "$command_line" == *"$metadata_model"* ]] || return 1
    [[ "$command_line" == *"--port $metadata_port"* || "$command_line" == *"--port=$metadata_port"* ]] || return 1
    return 0
}

service_port_is_listening() {
    local port="$1"
    command -v ss >/dev/null 2>&1 || return 2
    ss -ltn 2>/dev/null | awk -v wanted=":$port" '$4 == wanted || $4 ~ wanted"$" { found=1 } END { exit(found ? 0 : 1) }'
}

service_state=""
service_pid=""
service_detail=""

inspect_service_slot() {
    service_state=""
    service_pid=""
    service_detail=""

    if [[ ! -f "$pid_file" ]]; then
        if [[ -f "$metadata_file" ]]; then
            service_state="STALE_PID"
            service_detail="metadata exists without pid file"
        else
            service_state="NO_PID_FILE"
            service_detail="no owned pid metadata"
        fi
        return 0
    fi

    service_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ ! "$service_pid" =~ ^[0-9]+$ ]] || ! kill -0 "$service_pid" 2>/dev/null; then
        service_state="STALE_PID"
        service_detail="pid file does not reference a live process"
        return 0
    fi

    if ! read_service_metadata; then
        service_state="OWNERSHIP_MISMATCH"
        service_detail="metadata missing or does not match service slot"
        return 0
    fi
    if [[ "$metadata_pid" != "$service_pid" ]]; then
        service_state="OWNERSHIP_MISMATCH"
        service_detail="metadata pid does not match pid file"
        return 0
    fi
    if ! process_matches_metadata "$service_pid"; then
        service_state="OWNERSHIP_MISMATCH"
        service_detail="live process command line does not match model/port"
        return 0
    fi
    service_state="SERVICE_RUNNING"
    service_detail="owned vLLM process identity matches metadata"
}

remove_service_metadata() {
    rm -f -- "$pid_file" "$metadata_file"
}
