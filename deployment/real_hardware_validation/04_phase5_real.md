# 04 — Real Phase 5 probe

Run the existing live probe; do not write a second acceptance implementation.

```powershell
.venv\Scripts\python.exe scripts\real_hardware_smoke.py `
  --profile rtxpro6000_96g `
  --distro <optional-distro>
```

The smoke wrapper delegates to
`scripts/acceptance/blackwell_live_probe.py`. It assumes services are already
running and never starts or stops them. The probe must independently verify:

- Windows and WSL GPU identity and single-GPU consistency;
- exact Agent and dialogue `/v1/models` identity;
- real Agent JSON inference;
- dialogue non-stream and streaming inference through the production client;
- `enable_thinking=False` for the candidate;
- visible `<think>`, reasoning-field, and control-tag leakage;
- client TTFT, total latency, usage when available, and GPU snapshots.

Require `overall_status=PASS`, no leakage flags, and a preserved
`acceptance_summary.json` before running that profile's A/B arm. Any hard
failure leaves the arm incomplete; do not edit the artifact to continue.
