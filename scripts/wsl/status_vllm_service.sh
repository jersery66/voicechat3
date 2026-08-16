#!/usr/bin/env bash
set -euo pipefail

service_name=""
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

[[ -n "$service_name" ]] || { echo "--service-name is required" >&2; exit 2; }
# shellcheck source=/dev/null
source "$(dirname "$0")/vllm_service_identity.sh"
set_service_context "$service_name"
inspect_service_slot

printf 'service_name=%s\n' "$service_name"
printf 'state=%s\n' "$service_state"
printf 'pid=%s\n' "$service_pid"
printf 'pid_file=%s\n' "$pid_file"
printf 'metadata_file=%s\n' "$metadata_file"
printf 'detail=%s\n' "$service_detail"
if read_service_metadata; then
    printf 'model=%s\n' "$metadata_model"
    printf 'port=%s\n' "$metadata_port"
    printf 'metadata_pid=%s\n' "$metadata_pid"
else
    printf 'model=\nport=\nmetadata_pid=\n'
fi

# Status is an observer: lifecycle state is carried in the output, not in the
# process exit code.  Invalid arguments remain exit 2 above.
exit 0
