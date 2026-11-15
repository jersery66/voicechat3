# Engine services (llama.cpp / vLLM)

The application never starts, stops, or selects an inference engine. The
runtime configuration (`config/runtime.yaml`) names the models and the
loopback endpoints the application talks to; whoever starts the servers
starts them with the engine scripts below, one service per model.

## Layout

```
scripts/engines/llama_cpp/    llama-server services
scripts/engines/vllm/         vLLM services
```

Each engine directory provides the same four-script contract:

| Script | Role |
| --- | --- |
| `start_service.sh` | start one owned service, or prove the running one already matches |
| `stop_service.sh` | stop one owned service (SIGTERM, then identity-checked SIGKILL) |
| `status_service.sh` | observer: report slot state, pid, and metadata; never act |
| `service_identity.sh` | shared helpers: pid/metadata ownership and `/proc` identity |

## Ownership model

Both engines keep per-service state under `~/.voicechat/<engine>/`
(override with `VOICECHAT_LLAMA_CPP_RUNTIME_DIR` or
`VOICECHAT_VLLM_RUNTIME_DIR`):

- `<service>.pid` — the pid this tooling started.
- `<service>.meta` — `service_name`, `pid`, `model`, `port`, and the server
  executable recorded at start time.
- `<service>.log` — server stdout/stderr.

A lifecycle action is permitted only when the pid file, the metadata, and
the live `/proc/<pid>/cmdline` all agree on the service name, model, and
port. Every disagreement fails closed:

- `STALE_PID` — metadata without a live process: cleaned up, never killed.
- `OWNERSHIP_MISMATCH` — a live process that is not provably ours: never
  signaled.
- A listening port with no owned metadata: never replaced; start refuses.

`stop` never uses broad process patterns (`pkill`, `killall`, `taskkill`).
PID reuse is re-checked immediately before SIGKILL escalation.

## Starting a llama.cpp service

```bash
scripts/engines/llama_cpp/start_service.sh \
  --model /root/models/Qwen3.8-27B-Q4_K_S.gguf \
  --port 8000 \
  --alias Qwen3.8-27B-Q4_K_S \
  --context-size 8192 \
  --gpu-layers 99 \
  --parallel 1 \
  --chat-template-kwargs '{"enable_thinking":false}' \
  --service-name dialogue \
  --server-executable ~/.voicechat/llama.cpp/llama-server
```

The alias must equal `dialogue.model` in the runtime configuration: the
application and the acceptance probe require exact model identity from
`GET /v1/models`. A missing `--chat-template-kwargs` simply omits the flag.

`--check-executable` resolves and validates the executable and exits 0
without touching any service state; orchestration layers use it to prove
the engine exists before planning a start.

## Starting a vLLM service

```bash
scripts/engines/vllm/start_service.sh \
  --model Qwen/Qwen2.5-72B-Instruct-AWQ \
  --port 8000 \
  --gpu-memory-utilization 0.82 \
  --max-model-len 8192 \
  --service-name dialogue
```

## Status

```bash
scripts/engines/llama_cpp/status_service.sh --service-name dialogue
```

prints `state=`, `pid=`, `model=`, and `port=` and always exits 0 for a
well-formed request; lifecycle meaning lives in the printed state, not in
the exit code. `NO_PID_FILE` means this tooling owns nothing on that slot.

## Relationship to the runtime configuration

These scripts are engine conveniences, not configuration sources. The
single source of truth for what the application serves is
`config/runtime.yaml`; the probe (`scripts/acceptance/blackwell_live_probe.py`)
verifies that whatever is running matches it. Hardware-specific Windows
launchers from earlier iterations are archived under `archive/` and are not
part of the supported entry points.
