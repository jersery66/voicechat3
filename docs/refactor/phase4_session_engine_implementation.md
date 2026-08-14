# Phase 4 SessionEngine Implementation Record

## Status

Phase 4 is implemented and accepted on `codex/a100-vllm-safety`.  This record
covers only lifecycle ownership migration; Phase 5 policy and authority work
was not started.

## Baseline and implementation

- Baseline: `7c30305d5e3e43141212e7dfb04d5f1b9a4c8d96`
- Baseline tests: 394 passed, 0 failed, 0 skipped
- Implementation commit: `aa1a0416fc53cb0ce605be8da648ce40a0f058e1`
- Commit message: `refactor: make session engine authoritative`
- Remote branch at implementation push: `origin/codex/a100-vllm-safety` at
  `aa1a0416fc53cb0ce605be8da648ce40a0f058e1`

## Ownership migration

Before Phase 4, `MainWindow` held a second `SessionOrchestrator`, a second
`SessionEndController`, shadow end flags, and the legacy report/end reducer.
After Phase 4:

- `SessionEngine` is the only production lifecycle writer and runs its command
  handlers on the `session-engine` worker thread.
- `SessionLifecycleSnapshot` is a frozen lifecycle read model. It contains only
  session, playback, timeout, deferred-end, terminal, and boolean scale-activity
  projection data; it does not contain scale items, answers, pause details, or
  completion collections.
- `MainWindow` sends lifecycle commands and renders engine events. It no longer
  constructs or calls the legacy orchestrator/controller and no longer stores
  the removed lifecycle flags.
- `ConversationPipeline` has no stored relaxation/game/exit/finish lifecycle
  flags. Scale state remains in `ScaleRuntime`; report metadata remains in the
  report layer.
- Shadow switches `SESSION_ENGINE_SHADOW` and
  `SESSION_ENGINE_AUTHORITATIVE` were removed.

## Commands and events

The engine now handles start/end, relaxation and game playback, playback
completion, continue-chat, next-subject reset, exit, scale-activity projection,
report-completion, and time-limit check/acknowledgement commands. Commands that
belong to other adapters are explicitly rejected with `ErrorEvent` rather than
silently dropped. Existing lifecycle events drive UI rendering and report
startup. Deferred end requests remain in the engine until playback completion;
report generation remains before farewell TTS.

## Boundary and scenario coverage

`tests/test_phase4_lifecycle_boundary.py` covers the removed duplicate owners,
direct transitions, shadow switches, UI/Pipeline flag storage, immutable
snapshot shape, writer-thread events, scale projection, game/report completion,
time-limit ownership, and non-transitioning pipeline/report services. Existing
engine, headless UI, scale, and pipeline scenarios continue to cover start,
normal chat, relaxation/game playback, deferred and duplicate end requests,
timeout acknowledgement, report-first completion, reset, and consecutive
session behavior.

## Verification

- Full suite: **405 passed, 0 failed, 0 skipped** (`python -m pytest tests -q`)
- Targeted Phase 4 boundary/engine/UI/pipeline suite: passed
- `compileall` for `app`, `ui`, `services`, and `config.py`: PASS
- `git diff --check`: PASS
- A100/vLLM/STT/TTS/VAD deployment settings were not changed; the existing
  72B `127.0.0.1:8000` and 3B `127.0.0.1:8001` contracts remain intact.

## Local runtime smoke

The current development machine reports an NVIDIA RTX 3060 Laptop GPU with
6144 MiB and no installed `vllm` package. The configured FunASR/VoxCPM model
paths are deployment-machine paths, and the configured dialogue backend is
Ollama. Therefore real A100/vLLM/STT/TTS runtime smoke is explicitly:

**NOT RUN / environment unavailable.**

The local Python regression and offscreen headless UI tests above are the only
runtime checks claimed here; no deployment-machine result is inferred.

## Phase boundary

No `RouterProposal`, `TurnPolicy`, `TurnDecision`, `ScaleRuntime` ownership,
session-policy, or other Phase 5 contract was redesigned. No SessionEngine
turn-policy method, scale-answer owner, or new product rule was introduced.

## Working tree

The implementation commit was pushed with a clean tree and local/remote
`ahead/behind = 0/0`. A separate documentation-finalization commit follows
this record so the implementation SHA above remains an independent code
commit.
