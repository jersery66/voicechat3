# Windows + WSL2 Blackwell launcher

This launcher is the orchestration layer added from baseline
`9f6f55bd9ecefa94676eb47b1a8be766497cfe8f`.

## Runtime topology

- Windows owns the PySide6 UI, business runtime, FunASR/FSMN-VAD, VoxCPM2,
  data, and reports.
- WSL2 owns the two Linux vLLM services.
- The application reaches them through Windows-to-WSL localhost forwarding:
  dialogue `127.0.0.1:8000`, Agent `127.0.0.1:8001`.
- No `.wslconfig` changes, mirrored networking, portproxy, LAN binding, or
  `0.0.0.0` exposure are performed.

Windows owns the NVIDIA driver used by WSL2. The launcher only checks that the
selected WSL distribution reports Linux and that `nvidia-smi` executes; it
does not install Linux display drivers or alter driver state.

## Start

```powershell
.\scripts\windows\start_blackwell_stack.ps1 `
    -Profile rtxpro6000_96g `
    -DialogueGpuMemoryUtilization 0.55 `
    -AgentGpuMemoryUtilization 0.06
```

The default profile is `rtxpro6000_96g`. The Qwen3.8 profile is explicit
opt-in:

```powershell
.\scripts\windows\start_blackwell_stack.ps1 `
    -Profile rtxpro6000_96g_qwen38_candidate `
    -DialogueGpuMemoryUtilization <measured-value> `
    -AgentGpuMemoryUtilization <measured-value>
```

The launcher obtains model IDs, endpoints, and profile policy from
`DeploymentProfile`. It validates the selected profile, clears misleading
environment overrides, checks WSL and GPU visibility, starts Agent before
Dialogue, and requires an exact `/v1/models` identity on each loopback port.
GPU utilization values are mandatory operator inputs because no safe Blackwell
budget has been measured yet. The two values must each be between zero and one
and sum to less than one. Readiness waits are bounded by
`-StartupTimeoutMinutes` (20 by default).

If a port already serves the exact selected model, it is reused. A different
model on an occupied port is a hard failure; the launcher never kills or
replaces that service. Newly started services are tracked so partial startup
failure cleans up only services started by that invocation.

## WSL service lifecycle

The WSL service launcher uses `~/.voicechat/vllm/` for durable PID and log
files. It starts one `vllm serve` process per service with loopback binding,
bounded sequence count, prefix caching, and no tensor parallelism. Services
remain running after the Windows GUI exits and can be stopped explicitly:

```powershell
.\scripts\windows\stop_blackwell_stack.ps1
```

The stop path reads only the service PID files, sends a bounded TERM/KILL
sequence to those PIDs, and removes stale PID files. This is the manual
operator stop path; it does not use broad
`pkill`, `killall`, or `taskkill` patterns.

After both exact models are ready, the launcher runs the existing
`scripts/check_config.py` with the selected strict profile. Only a successful
check starts the Windows `main.py`; PySide6 is never launched inside WSL.

## Validation boundary

The deterministic launcher contract is covered by tests. This commit does not
claim real deployment validation:

```text
Launcher contract tests: TESTED
Actual WSL runtime:      NOT RUN
Actual nvidia-smi:       NOT RUN
RTX PRO 6000 identity:   NOT VERIFIED
vLLM:                    NOT RUN
Qwen2.5-72B:             NOT RUN
Qwen3.8-27B-FP8:         NOT RUN
VoxCPM2 coexistence:     NOT RUN
VRAM budget:             NOT RUN
TTFT/tokens/sec:         NOT RUN
```

The legacy A100 PowerShell launchers remain unchanged and outside this
launcher. Live dialogue probes, GPU/VRAM approval, and model A/B testing are
later work.
