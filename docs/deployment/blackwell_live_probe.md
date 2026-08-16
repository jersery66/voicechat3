# Blackwell live deployment probe

This acceptance-only tool was added from baseline
`d5c47b46a95193c5a0ad3d828e6cd30bd2df1281`.

It assumes that `scripts/windows/start_blackwell_stack.ps1` has already
started the selected services. The probe never starts, stops, restarts, or
reconfigures vLLM and never changes deployment profiles or GPU budgets.

## Run

From Windows, with the launcher-managed services already ready:

```powershell
.venv\Scripts\python.exe scripts\acceptance\blackwell_live_probe.py `
    --profile rtxpro6000_96g
```

The candidate remains explicit opt-in:

```powershell
.venv\Scripts\python.exe scripts\acceptance\blackwell_live_probe.py `
    --profile rtxpro6000_96g_qwen38_candidate
```

`--distro`, `--timeout-seconds`, `--output-root`, and
`--vllm-executable` are optional. Only the two RTX PRO 6000 profiles are
accepted; the profile remains the operator-selected source of model IDs,
endpoints, and generation settings.

## Evidence collected

Each run creates a timestamped directory under the ignored
`test_output/blackwell_acceptance/` tree. It records Windows and WSL
`nvidia-smi` observations, the single-GPU and RTX PRO 6000 Blackwell identity
checks, exact `/v1/models` identities, direct Agent JSON inference, dialogue
non-stream and production `VLLMOpenAIClient.stream_messages()` inference,
client-side TTFT/total latency, usage when available, optional vLLM
per-request metrics, and before/after GPU snapshots.

The candidate requires the profile-owned `enable_thinking=False` request
contract. Raw responses are inspected for non-empty reasoning fields,
`<think>` markup, and legacy control markers. Any such leakage is a hard
failure; the probe never strips it and calls the request successful.

Server-side per-request metrics and `/metrics` are opportunistic. Missing
metrics are recorded as `UNAVAILABLE / NOT ENABLED` and do not fail an
otherwise valid inference. Client-side timing is always collected.

The production-client smoke calls and the raw acceptance calls are separate
and explicitly labeled in the artifacts. The smoke calls prove that the
profile-built `VLLMOpenAIClient` works. The raw streaming acceptance call is
the sole owner of its own request start, first-content timestamp, stream end,
visible content, usage, throughput, and raw reasoning/control-tag inspection.
Metrics from separate requests are never combined, so TTFT and token usage
remain request-consistent. A WSL `nvidia-smi` command-not-found result is
retried through `/usr/lib/wsl/lib/nvidia-smi`; no driver or WSL configuration
is changed.

Acceptance summaries copy raw-stream measurements and leakage flags before the
hard-fail gate. If reasoning, thinking markup, or a control tag is detected,
both `acceptance_summary.json` and `dialogue_stream.json` report the raw step
as `FAIL`; the summary never falls back to `NOT RUN` or stores hidden text.

The current acceptance contract rejects multiple visible NVIDIA GPUs rather
than guessing GPU 0. It compares Windows and WSL GPU family and memory, but
does not approve VRAM budgets or impose TTFT/throughput thresholds.

## Privacy and scope

Prompts are fixed synthetic connectivity strings. The probe does not load
participant history, DataManager data, clinical scores, microphone/STT, or
audio/TTS providers, and it writes no participant session or report.
Only localhost endpoints (`127.0.0.1:8000` and `127.0.0.1:8001`) are queried.

## Validation status at implementation time

```text
Live probe implementation: READY
Deterministic probe tests: TESTED
Real Blackwell live probe: NOT RUN
RTX PRO 6000 identity:    NOT VERIFIED
Windows nvidia-smi:       NOT RUN
WSL nvidia-smi:            NOT RUN
Real Qwen2.5:             NOT RUN
Real Qwen3.8:             NOT RUN
Real Agent:               NOT RUN
Real vLLM inference:      NOT RUN
VoxCPM2/STT coexistence:  NOT TESTED BY THIS PROBE
```

This tooling does not promote Qwen3.8, freeze memory utilization, perform
quality A/B comparisons, or replace the later full-system hardware and audio
acceptance.
