# Phase 3 implementation record — migrate all scale state to ScaleRuntime

Status: COMPLETE. Phase 3 only; Phase 4/5 work was not started.

## Baseline

- Branch: `codex/a100-vllm-safety`
- Implementation baseline: `e5c4a38b8e0775f0919b4e9fc70bd0dd44270747`
- Baseline test result before production edits: `351 passed`, `0 failed`, `0 skipped`

## Implemented ownership boundary

- `assessment/scale_runtime.py` now owns active scale, current item, waiting,
  per-scale answers, administered/completed scales, pause/resume, reset, and
  immutable snapshots/results.
- `services/scales.py` exposes definition-backed item counts, legal scores,
  validation, and invalid-score reporting directly from `SCALES`.
- `assessment/answer_interpreter.py` separates natural-language answer
  interpretation from deterministic Runtime mutation.
- `services/pipeline.py` applies the existing single `TurnDecision` through
  Runtime commands and has no `ScaleState`/delegate-property or answer-map
  shadow owner. Tags are candidates only; only a validated current-item score
  reaches `ScaleRuntime.accept_answer()`.
- Pipeline/UI/report compatibility facades read Runtime snapshots/results;
  existing relaxation/session lifecycle was not redesigned.
- `core/scale_fsm.py` remains compatibility-only and is not imported by the
  production Pipeline.

## Changed files

| Path | Reason |
|---|---|
| `assessment/__init__.py` | Export Runtime snapshots, updates, incomplete views, and interpreter contracts. |
| `assessment/answer_interpreter.py` | Add pure accepted/ambiguous/pause/unmatched answer interpretation. |
| `assessment/scale_policy.py` | Read registered scale names from the canonical manager and document Runtime ownership. |
| `assessment/scale_runtime.py` | Implement deterministic single-owner state, legal answers, pause/resume, completion, and read models. |
| `core/scale_fsm.py` | Mark the old container compatibility-only and derive its legacy names from `SCALES`. |
| `services/pipeline.py` | Migrate decisions, answer acceptance, snapshots, resume, and reports to `ScaleRuntime`. |
| `services/scales.py` | Add immutable definition accessors and definition-backed score validation. |
| `tests/test_assessment_runtime.py` | Update legacy assessment tests to the Runtime-only item contract. |
| `tests/test_core_scale_fsm.py` | Prove the old container is not production-owned. |
| `tests/test_turn_authority_pipeline.py` | Assert Phase 2 authority observes Runtime snapshots. |
| `tests/integration/test_pipeline_e2e.py` | Assert current-item-only acceptance and Runtime-derived state. |
| `tests/test_scale_answer_interpreter.py` | Cover clear, ambiguous, pause, unmatched, and no-mutation answers. |
| `tests/test_scale_definitions.py` | Cover canonical domains, item counts, maxima, and invalid scores. |
| `tests/test_scale_pipeline_boundary.py` | Cover decision-to-Runtime commands and read facades. |
| `tests/test_scale_runtime.py` | Cover deterministic Runtime invariants and immutable snapshots. |
| `tests/test_scale_state_boundary.py` | Enforce no legacy owner, second authority, or Phase 4/5 contract. |

## Verification

Final command:

```text
E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe -m pytest tests -q
394 passed in 63.99s
0 failed, 0 skipped
```

Additional checks:

- `python -m compileall -q assessment core services tests`: PASS
- `git diff --check`: PASS (no whitespace errors)
- Phase 1 safety-boundary tests: PASS
- Phase 2 authority tests: PASS
- Runtime/pipeline/definition/interpreter/boundary focused tests: PASS

## Git result

- Implementation commit: `2967fc09344b3d6a4fc0395e299b4fba29b76e62`
- Commit message: `refactor: migrate all scale state to scale runtime`
- Implementation push: `origin/codex/a100-vllm-safety` accepted the commit.
- Post-push implementation verification: local HEAD and remote HEAD both
  `2967fc09344b3d6a4fc0395e299b4fba29b76e62`; ahead/behind `0/0`; working tree
  was clean before this documentation finalization.

## Runtime smoke status

- Local Python import/compile and the complete test suite: **RUN / PASS**.
- Real A100/vLLM dialogue services on `127.0.0.1:8000` and `127.0.0.1:8001`:
  **NOT RUN / environment unavailable**.
- Real FunASR STT device/model: **NOT RUN / environment unavailable**.
- Real VoxCPM2 TTS device/model/audio playback: **NOT RUN / environment unavailable**.

No A100/vLLM, STT, TTS, deployment, or desktop architecture was changed.

## Scope stop

No SessionEngine authority migration, Router/TurnDecision redesign,
authoritative session migration, ScaleRuntime action policy, or other Phase 4/5
production contract was introduced.
