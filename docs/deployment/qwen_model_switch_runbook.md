# Baseline / candidate model switch runbook

The workstation keeps one Agent service and exactly one dialogue model:

```text
Agent :8001 + ONE dialogue model :8000
```

The A/B harness never starts, stops, or switches services.

## Baseline to candidate

1. Save the baseline deployment-doctor, Phase 5, and A/B artifacts.
2. Finish or cancel the active participant/session generation safely.
3. Stop the owned dialogue service through the Windows stop contract.
4. Verify its owned PID and metadata exited cleanly.
5. Verify `127.0.0.1:8000` is released and record a memory snapshot.
6. Start `rtxpro6000_96g_qwen38_candidate` with explicit operator-supplied
   memory arguments.
7. Require exact `Qwen/Qwen3.8-27B-FP8` identity at `:8000`.
8. Run the candidate Phase 5 probe; stop on any hard failure or leakage.
9. Run the candidate A/B arm using the candidate probe summary.
10. Compare only when both arms have matching commit/prompt/scenario hashes.

## Candidate to baseline

Use the same sequence in reverse. Never keep baseline and candidate dialogue
servers resident simultaneously on the single-GPU contract.

## Safety rules

- An unknown port owner is inspected manually and never auto-killed.
- A wrong model identity is a hard failure; response success is insufficient.
- A stale PID file may be cleaned only in its own service slot.
- `promotion_status` remains `NOT APPROVED` until human review and real
  evidence support a recommendation.
