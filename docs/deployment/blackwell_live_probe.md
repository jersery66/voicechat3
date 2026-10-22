# Live deployment probe

This acceptance-only tool was added from baseline
`d5c47b46a95193c5a0ad3d828e6cd30bd2df1281`. The file name is historical.

It assumes the configured services are already running (see
`docs/deployment/engine_services.md`). The probe never starts, stops,
restarts, or reconfigures an inference server and never selects a model: the
runtime configuration is the operator-selected source of model IDs,
endpoints, providers, and generation settings.

## Run

From Windows, with the configured services already ready:

```powershell
.venv\Scripts\python.exe scripts\acceptance\blackwell_live_probe.py `
    --config config\runtime.yaml
```

`--config` defaults to `VOICECHAT_RUNTIME_CONFIG`, then to
`config/runtime.yaml`. To probe a different configuration — the other arm of
a model comparison, for example — point at that file:

```powershell
.venv\Scripts\python.exe scripts\acceptance\blackwell_live_probe.py `
    --config config\runtime_candidate.yaml
```

`--distro`, `--timeout-seconds`, `--output-root`, and `--server-executable`
are optional. `--server-executable` only enables the server version record; if
it is omitted the version is reported as unavailable instead of failing the
run. Configurations that are not explicit, loopback-only, and served by a
supported provider are rejected before any request is made.

## Evidence collected

Each run creates a timestamped directory under the ignored
`test_output/live_acceptance/` tree. It records Windows and WSL `nvidia-smi`
observations, the single-GPU check, exact `/v1/models` identities, direct
Agent JSON inference, dialogue non-stream and production
`OpenAICompatibleClient.stream_messages()` inference, client-side
TTFT/total latency, usage when available, optional server per-request metrics,
and before/after GPU snapshots.

When `agent.enabled` is `false`, the Agent checks are recorded as `SKIPPED`
rather than failed, and its endpoint is not probed.

When `dialogue.enable_thinking` is `false`, the probe requires the request to
actually carry that setting. Raw responses are inspected for non-empty
reasoning fields, `<think>` markup, and legacy control markers. Any such
leakage is a hard failure; the probe never strips it and calls the request
successful.

Server-side per-request metrics and `/metrics` are opportunistic. Missing
metrics are recorded as `UNAVAILABLE / NOT ENABLED` and do not fail an
otherwise valid inference. Client-side timing is always collected.

The production-client smoke calls and the raw acceptance calls are separate
and explicitly labeled in the artifacts. The smoke calls prove that the
config-built `OpenAICompatibleClient` works. The raw streaming acceptance call
is the sole owner of its own request start, first-content timestamp, stream
end, visible content, usage, throughput, and raw reasoning/control-tag
inspection. Metrics from separate requests are never combined, so TTFT and
token usage remain request-consistent. A WSL `nvidia-smi` command-not-found
result is retried through `/usr/lib/wsl/lib/nvidia-smi`; no driver or WSL
configuration is changed.

Acceptance summaries copy raw-stream measurements and leakage flags before the
hard-fail gate. If reasoning, thinking markup, or a control tag is detected,
both `acceptance_summary.json` and `dialogue_stream.json` report the raw step
as `FAIL`; the summary never falls back to `NOT RUN` or stores hidden text.

GPU observation rejects multiple visible NVIDIA GPUs rather than guessing GPU
0, and compares the Windows and WSL view. It does not decide whether a
deployment may run: an out-of-memory condition surfaces as the inference
server's own error, not as a configured VRAM threshold.

The summary records `runtime_config` (the configuration file name) and
`dialogue_model`; the A/B harness matches its arms on exactly these fields.

## Privacy and scope

Prompts are fixed synthetic connectivity strings. The probe does not load
participant history, DataManager data, clinical scores, microphone/STT, or
audio/TTS providers, and it writes no participant session or report.
Only loopback endpoints from the runtime configuration are queried.

## Validation status at implementation time

```text
Live probe implementation: READY
Deterministic probe tests: TESTED
Real live probe:          OPERATOR RUN REQUIRED
Real identity check:      OPERATOR RUN REQUIRED
Windows/WSL nvidia-smi:   OPERATOR RUN REQUIRED
Real dialogue model:      OPERATOR RUN REQUIRED
Real Agent:               OPERATOR RUN REQUIRED
VoxCPM2/STT coexistence:  NOT TESTED BY THIS PROBE
```

This tooling does not promote a model, freeze memory utilization, perform
quality A/B comparisons, or replace the later full-system hardware and audio
acceptance.
