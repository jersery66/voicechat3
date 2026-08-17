# 07 — VRAM coexistence checkpoints

Use the existing memory snapshot tool and raw `nvidia-smi` output. Do not
freeze a budget before measuring the target workstation.

Capture at least:

```text
HOST_BASELINE
AGENT_LOADED
AGENT_PLUS_DIALOGUE
VOXCPM_LOADED
FUNASR_LOADED
FULL_STACK_IDLE
FULL_STACK_ACTIVE
```

For every checkpoint record GPU name/index, total/used/free memory, process
owners, CPU RAM when available, profile, git commit, timestamp, and whether
the evidence is `MEASURED`. Include snapshots during LLM generation, ASR,
and TTS playback. A CUDA OOM or model eviction stops the current arm; do not
silently shrink the model or alter profile parameters.
