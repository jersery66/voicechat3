# Real RTX PRO 6000 validation package

This directory is the operator handoff for the corrected pre-hardware
candidate. It is a validation procedure, not a new production phase and not a
runtime architecture. The original freeze remains available for historical
comparison; the workstation candidate is the corrected tag below.

```text
original freeze:
  pre-hardware-freeze-20260817
  -> 32793d300662fd53d462d30cbebc071348544890

corrected workstation candidate:
  pre-hardware-corrected-20260817
  -> e4de593321a6334099971ac5a0d26c9141c419b4
```

The candidate is still unvalidated. RTX identity, WSL CUDA, vLLM model load,
VRAM coexistence, Phase 5, A/B, STT, TTS, and end-to-end audio are all
`NOT RUN` until the target Windows workstation produces measured artifacts.

## Run order

1. Protect the corrected tag and verify the clean checkout.
2. Run `01_gpu_preflight.md` and `02_wsl_cuda_vllm.md`.
3. Start Agent first, then exactly one dialogue model as described in
   `03_model_startup.md`.
4. Run the existing live probe through `04_phase5_real.md`.
5. Run the baseline A/B arm, switch services explicitly, then run the
   candidate arm using `05_ab_real.md`.
6. Add STT, TTS, and the full audio path only after the text path is stable;
   follow `06_stt_tts_e2e.md`.
7. Record every memory checkpoint and 20--30 turn stability run using
   `07_vram_coexistence.md` and `08_long_session_stability.md`.
8. Complete `09_results_template.md` from raw command output and generated
   JSON artifacts.

## Existing authoritative tools

This package does not replace the frozen tools:

- `scripts/real_hardware_preflight.py` — read-only host/WSL/PyTorch/vLLM
  environment check; it does not start services.
- `scripts/real_hardware_smoke.py` — thin operator wrapper around the existing
  `scripts/acceptance/blackwell_live_probe.py`; it does not start or stop
  services.
- `scripts/windows/start_blackwell_stack.ps1` — sole service launcher.
- `scripts/windows/stop_blackwell_stack.ps1` — owned-service shutdown only.
- `scripts/deployment/doctor.py` — read-only deployment diagnostics.
- `scripts/acceptance/qwen_dialogue_ab.py` — comparison only; no service
  lifecycle.

No script in this package selects a GPU, changes `.wslconfig`, installs a
driver, downloads a model, tunes memory utilization, or promotes Qwen3.8.

## Evidence boundary

Use `MEASURED` only for values returned by the target machine. Mock or fixture
checks remain `SIMULATED`; unavailable hardware remains `NOT RUN` or
`NOT AVAILABLE`. Never hand-edit an acceptance artifact or convert a software
contract result into a hardware result.
