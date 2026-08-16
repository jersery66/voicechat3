# RTX PRO 6000 Blackwell deployment profiles

This document records the explicit deployment profiles added from baseline
`4b88726cc207dd333f0abe71fa0f101a3e7ee7bb`.

## Target hardware (assumed, not verified)

- GPU: NVIDIA RTX PRO 6000 Blackwell Workstation Edition
- Memory: 96 GB
- Host: Windows 11
- Intended later topology: Windows desktop runtime with vLLM services in WSL2,
  using loopback endpoints from the application.

The hardware identity is a deployment target assumption. It has not been
verified with `nvidia-smi`.

## Profiles

### `rtxpro6000_96g`

This is the RTX PRO 6000 baseline profile. It keeps the existing Qwen2.5
production contract:

- Dialogue: `Qwen/Qwen2.5-72B-Instruct-AWQ`
- Router/Agent: `Qwen/Qwen2.5-3B-Instruct-AWQ`
- Dialogue endpoint: `http://127.0.0.1:8000/v1`
- Agent endpoint: `http://127.0.0.1:8001/v1`
- Chat/native vLLM request mode
- 1024 dialogue token budget
- Temperature `0.35`, top-p `0.8`

### `rtxpro6000_96g_qwen38_candidate`

This is an explicit opt-in candidate only. It does not become the default and
is not promoted to production:

- Dialogue: `Qwen/Qwen3.8-27B-FP8`
- Router/Agent: `Qwen/Qwen2.5-3B-Instruct-AWQ`
- Dialogue endpoint: `http://127.0.0.1:8000/v1`
- Agent endpoint: `http://127.0.0.1:8001/v1`
- Temperature `0.7`, top-p `0.8`, top-k `20`, presence penalty `1.5`
- Non-thinking mode

Both profiles declare `immutable_runtime_contract=True` and
`strict_preflight=True`. Existing profile-owned configuration and factory
logic therefore pins the model and loopback endpoint values without adding
hardware-name special cases.

The legacy `a100_80g` and `a100_80g_qwen38_candidate` profiles remain
unchanged for compatibility. They are not aliases for the new profiles.

## Validation status

The profile and factory contracts are covered by deterministic tests. Real
hardware and deployment validation has not been run:

```text
Exact GPU identity:       NOT VERIFIED
nvidia-smi:               NOT RUN
WSL2 CUDA:                NOT RUN
vLLM:                     NOT RUN
Qwen2.5-72B:              NOT RUN
Qwen3.8-27B-FP8:          NOT RUN
3B Agent:                NOT RUN
VoxCPM2 coexistence:      NOT RUN
VRAM:                     NOT RUN
TTFT:                     NOT RUN
tokens/sec:               NOT RUN
```

GPU memory-utilization values, WSL launchers, live probes, strict `main.py`
fail-closed startup, and model promotion remain separate later work.
