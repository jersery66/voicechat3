# 09 — Results template

Fill this file only from target-machine commands and generated artifacts.

```text
REAL HARDWARE VALIDATION — CHECKPOINT
date/time:
operator:
git_commit:
profile:

Windows
- edition/build:
- exact GPU:
- driver:
- VRAM total/free:
- raw nvidia-smi artifact:

WSL/CUDA
- distro:
- WSL version:
- WSL nvidia-smi:
- torch version:
- cuda_available:
- CUDA version:
- detected device:
- vLLM version:

Service identity
- Agent model / :8001:
- dialogue model / :8000:
- Agent+dialogue coexistence:

Phase 5
- hardware status:
- Agent inference:
- dialogue non-stream:
- dialogue stream:
- thinking/reasoning/control leakage:
- TTFT / first sentence / total latency:
- artifact directory:

A/B
- baseline status:
- candidate status:
- comparison status:
- blind packet artifact:
- human review:

STT/TTS/E2E
- real STT:
- real TTS:
- first audio:
- full E2E:
- long-session stability:

Overall
- REAL HARDWARE VALIDATION: NOT RUN / PASS / FAIL
- QWEN3.8 PROMOTION: NOT APPROVED
- blockers and raw evidence:
- next safe action:
```

Never fill `PASS` from a fixture, a code test, or an unavailable command.
