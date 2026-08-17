# Pre-Hardware release inventory

This inventory describes the frozen software package before real workstation
acceptance. It is not a hardware result.

## Version and profiles

- Branch: `codex/a100-vllm-safety`
- Pre-hardware starting baseline: `6b626aec9813906b7a844cead058743f0aea56cc`
- Original freeze identity: Git tag `pre-hardware-freeze-20260817` (historical)
- Corrected workstation candidate: Git tag `pre-hardware-corrected-20260817`
  pointing to `e4de593321a6334099971ac5a0d26c9141c419b4`
- Immutable validation handoff: Git tag `pre-hardware-validation-ready-v2-20260817`
  (created on the final closure commit)
- Functional Batch 5 content commit: `83e48184e304150a380c7fcac44b21064a565779`
- Baseline profile: `rtxpro6000_96g`
- Candidate profile: `rtxpro6000_96g_qwen38_candidate`
- Candidate promotion: `NOT APPROVED`

## Model/runtime contract

- Baseline dialogue: `Qwen/Qwen2.5-72B-Instruct-AWQ`
- Candidate dialogue: `Qwen/Qwen3.8-27B-FP8`
- Agent: profile-owned `Qwen/Qwen2.5-3B-Instruct-AWQ`
- Dialogue endpoint: `127.0.0.1:8000`
- Agent endpoint: `127.0.0.1:8001`
- STT: `services.stt_service.STTService` / Fun-ASR-Nano configuration
- VAD: `services.fsmn_vad_adapter.FSMNVADAdapter` / `fsmn-vad`
- TTS: `services.tts_service_voxcpm.TTSService` / VoxCPM2

## Entrypoints and documents

- Windows start/verify: `scripts/windows/start_blackwell_stack.ps1`
- Windows stop: `scripts/windows/stop_blackwell_stack.ps1`
- Deployment doctor: `scripts/deployment/doctor.py`
- Manifest generator: `scripts/deployment/manifest.py`
- Offline gate: `scripts/acceptance/offline_integration.py`
- Final gate: `scripts/deployment/final_readiness.py`
- Phase 5 probe: `scripts/acceptance/blackwell_live_probe.py`
- A/B harness: `scripts/acceptance/qwen_dialogue_ab.py`
- Real-machine preflight: `scripts/real_hardware_preflight.py`
- Real-machine smoke wrapper: `scripts/real_hardware_smoke.py`
- Validation package: `deployment/real_hardware_validation/README.md`
- Operator runbook: `rtxpro6000_operator_runbook.md`
- First-machine checklist: `first_machine_checklist.md`
- Switch/recovery/troubleshooting/map documents in this directory

## Explicitly hardware-blocked

GPU identity, WSL CUDA, vLLM model load, VRAM coexistence, Phase 5, real A/B,
human review, real STT, real TTS, and real E2E are all `NOT RUN`.
