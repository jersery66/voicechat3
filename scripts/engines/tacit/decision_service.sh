#!/usr/bin/env bash
# Lifecycle wrapper for the local decision service.
#
#   ./decision_service.sh status  --name tacit-1.7b
#   ./decision_service.sh start   --name tacit-1.7b --model-path /models/Tacit-1.7B
#   ./decision_service.sh health  --name tacit-1.7b
#   ./decision_service.sh restart --name tacit-1.7b --model-path /models/Tacit-1.7B
#   ./decision_service.sh recover --name tacit-1.7b --model-path /models/Tacit-1.7B
#   ./decision_service.sh stop    --name tacit-1.7b [--force]
#
# The real work happens in scripts/decision/service_ctl.py so that Windows and
# WSL2 share one identity/ownership implementation.  This wrapper only resolves
# an interpreter and forwards arguments.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/../../.." && pwd)"

usage() {
    cat >&2 <<'USAGE'
Usage: decision_service.sh <start|stop|status|restart|recover|health> [options]

Common options:
  --name NAME          service slot name (default: tacit)
  --model-path DIR     local checkpoint directory (start/restart/recover)
  --manifest FILE      integrity manifest (default: <model>/tacit_manifest.json)
  --port PORT          force a port instead of allocating a free one
  --python PATH        interpreter of the isolated decision venv
  --timeout SECONDS    health-wait budget (default: 600)
  --skip-verify        skip the integrity manifest check (unsafe)
  --force              stop even when ownership cannot be verified

Environment:
  VOICECHAT_TACIT_PYTHON       interpreter override
  VOICECHAT_TACIT_RUNTIME_DIR  pid/metadata/log directory (default: ~/.voicechat/tacit)
USAGE
}

if [[ $# -lt 1 ]]; then
    usage
    exit 2
fi

command="$1"
shift

case "$command" in
    start|stop|status|restart|recover|health) ;;
    -h|--help|help)
        usage
        exit 0
        ;;
    *)
        echo "Unknown command: $command" >&2
        usage
        exit 2
        ;;
esac

python_bin="${VOICECHAT_TACIT_PYTHON:-}"
if [[ -z "$python_bin" ]]; then
    if [[ -x "$project_root/.venv/Scripts/python.exe" ]]; then
        python_bin="$project_root/.venv/Scripts/python.exe"
    elif [[ -x "$project_root/.venv/bin/python" ]]; then
        python_bin="$project_root/.venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        python_bin="$(command -v python3)"
    elif command -v python >/dev/null 2>&1; then
        python_bin="$(command -v python)"
    else
        echo "No python interpreter found; set VOICECHAT_TACIT_PYTHON" >&2
        exit 2
    fi
fi

has_name=0
for arg in "$@"; do
    if [[ "$arg" == "--name" ]]; then
        has_name=1
        break
    fi
done

extra=()
if [[ $has_name -eq 0 ]]; then
    extra+=(--name tacit)
fi

exec "$python_bin" "$project_root/scripts/decision/service_ctl.py" "$command" "${extra[@]}" "$@"
