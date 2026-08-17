# Deployment troubleshooting guide

Use this guide together with the shutdown/recovery runbook. Symptoms can have
multiple causes; the listed subsystem is a starting point, not a diagnosis.

| Symptom | Diagnostic commands/artifacts | Likely subsystem | Safe next action |
| --- | --- | --- | --- |
| `wsl.exe` not found | `Get-Command wsl.exe`; `wsl --status` | Windows/WSL | install/enable WSL through IT process |
| WSL sees no GPU | `wsl -- nvidia-smi`; `/usr/lib/wsl/lib/nvidia-smi` | Windows driver/WSL | stop before vLLM; record raw output |
| `nvidia-smi` unavailable | host and WSL raw command output | driver/PATH | do not infer GPU identity |
| `torch.cuda.is_available() == False` | WSL Python torch probe | CUDA/PyTorch | preserve versions and diagnose environment |
| vLLM command missing | configured WSL executable; `--check-executable` | WSL Python env | install/activate the operator-approved env |
| model load fails | service log, model path/cache, GPU snapshot | vLLM/model/CUDA | stop; classify root cause before tuning |
| CUDA OOM | vLLM log, before/after memory snapshots | GPU budget/model fit | stop owned service; do not change profile silently |
| port 8000 occupied | `-VerifyOnly`, endpoint `/v1/models`, PID metadata | process/port | identify unknown owner manually |
| port 8001 occupied | same as above | process/port | never broad-kill |
| wrong model identity | `/v1/models`, selected profile manifest | profile/service | fail closed and correct operator selection |
| Agent API unavailable | `:8001/v1/models`, Agent log | Agent/vLLM | start Agent first and re-run readiness |
| dialogue API unavailable | `:8000/v1/models`, dialogue log | dialogue/vLLM | inspect owned service and timeout log |
| GUI refuses startup | strict preflight output, readiness summary | config/profile | fix failed preflight; do not bypass |
| Phase 5 raw stream failure | `acceptance_summary.json`, stream artifact | model/vLLM/client | keep arm incomplete |
| thinking/control leakage | Phase 5 leakage flags | dialogue model/request | fail candidate; do not strip evidence |
| TTS has no output | application/TTS logs, delivery events | VoxCPM2/audio | mark real TTS NOT RUN/failed; inspect device |
| STT empty result | STT lifecycle logs, final transcript artifact | FunASR/VAD/device | repeat synthetic check, then real device test |
| stale audio | generation/delivery events | cancellation/delivery | inspect stale generation; do not edit history manually |
| latency abnormal | measurement JSONL, memory snapshots | model/GPU/audio | report distributions; no unvalidated threshold |

Artifact locations are listed in `artifact_location_map.md`.
