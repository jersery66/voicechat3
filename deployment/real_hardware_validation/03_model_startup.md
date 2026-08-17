# 03 — Agent and dialogue startup

The Windows launcher is the only service lifecycle owner. Keep one Agent and
one dialogue model resident on the single-GPU contract.

## Agent first

Use explicit, operator-measured memory arguments; no value is approved in
advance:

```powershell
.\scripts\windows\start_blackwell_stack.ps1 `
  -Profile rtxpro6000_96g `
  -DialogueGpuMemoryUtilization <measured-value> `
  -AgentGpuMemoryUtilization <measured-value>
```

The launcher starts Agent `:8001`, waits for the exact
`Qwen/Qwen2.5-3B-Instruct-AWQ` identity, then starts dialogue `:8000` and
waits for the profile-owned identity. It runs strict preflight and launches
the Windows GUI last.

Use `-VerifyOnly` (or `-Status`) for a read-only inspection. An unknown port
owner, wrong model, stale ownership, or strict-preflight failure is
fail-closed; never kill an unknown process.

## Baseline and candidate

Baseline is explicit `rtxpro6000_96g` with
`Qwen/Qwen2.5-72B-Instruct-AWQ`. Candidate is explicit
`rtxpro6000_96g_qwen38_candidate` with `Qwen/Qwen3.8-27B-FP8` and
`enable_thinking=False`. Stop the baseline dialogue service and verify PID,
port, and VRAM release before starting the candidate. Never keep both dialogue
models resident simultaneously.

The A/B harness, live probe, doctor, and GUI are observers/consumers. They do
not start, stop, restart, or switch services.
