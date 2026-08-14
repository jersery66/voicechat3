# Phase 5 implementation record: unify relaxation, game, end, and timeout routing

## Status

- Phase 5 production implementation: complete locally.
- Production implementation commit: `95c8bb42134e9a7ff0eaa7a2d1fa6a812eb49fb8`
  (`refactor: unify relaxation game end and timeout routing`).
- Phase 5 design baseline: `da708a4068520de27bee086d14724570810b6ef0`.
- Branch: `codex/a100-vllm-safety`.
- Remote push/HEAD verification: pending while the current environment cannot
  connect to GitHub; no remote result is claimed until a push succeeds.

This record is intentionally separate from the production implementation
commit. It records the verified local result and will be finalized after the
implementation push. The Phase 5 specification and inventory remain the
authoritative design documents.

## Scope implemented

Phase 5 keeps the frozen authority chain:

```text
RouterProposal -> TurnPolicy -> exactly one TurnDecision
       -> SessionEngine / ScaleRuntime execution -> MainWindow rendering
```

- Explicit relaxation requests are distinguished from proactive candidates;
  user requests can be approved before the minimum-round threshold, while
  proactive recommendations require eight rounds, cannot occur while waiting
  for a scale answer, and are offered at most once per session.
- Game execution requires an explicit user request. Boredom and entertainment
  context remain chat/context signals and cannot authorize `RECOMMEND_GAME`.
- Deterministic explicit-end phrases produce one `END_SESSION` decision.
  Weak acknowledgements and positive post-relaxation feedback do not end a
  session. Explicit end bypasses readiness prompts and forced relaxation.
- SessionEngine remains the only lifecycle writer. It executes approved end
  commands, safely defers an end during active media, and owns timeout warning,
  hard-limit ask, and continue acknowledgement markers.
- Pipeline no longer emits a competing timeout ask or reads the legacy report
  timeout marker as a second policy. UI no longer mirrors timeout markers into
  `ReportService`.
- Relaxation and game media remain execution paths. Failed media is not
  recorded as completed; successful media continues through the existing
  post-intervention event.
- A paused questionnaire resumes through `ScaleRuntime` and its actual
  current item. UI-cached item numbers and legacy readiness flags cannot
  authorize or restore scale state.
- 72B `[END_*]`/`[REC_*]` tags remain metadata only; Router output remains a
  proposal and cannot choose an item or score.

## Changed files

### Production

- `app/contracts.py` — marks `allow_force_relaxation` as ignored wire-compatibility
  metadata and defaults it to no-force behavior.
- `app/engine.py` — removes engine-side forced-relaxation policy and keeps
  lifecycle-only end execution plus one-shot timeout ownership.
- `conversation/contracts.py` — adds explicit relaxation/game and proactive
  recommendation signal fields and the one-shot proactive-offer snapshot fact.
- `conversation/turn_signals.py` — collects pure explicit end, relaxation, and
  game request observations.
- `conversation/turn_policy.py` — centralizes the Phase 5 eligibility matrix
  and rejects Router-only game/end/relaxation proposals when signals do not
  authorize them.
- `core/scoring.py` — retains weak-response exclusions and recognizes the
  explicit `结束` signal.
- `core/session_fsm.py` — narrows the legacy end facade to a lifecycle
  transition and removes forced-relaxation state/decision logic.
- `services/agent_service.py` — updates Router guidance so boredom does not
  recommend a game.
- `services/pipeline.py` — removes legacy relaxation/game policy shadow flags,
  delegates timeout ownership to SessionEngine, preserves authoritative tags,
  and exposes Runtime-owned post-relaxation resume.
- `ui/main_window.py` — routes approved decisions directly, removes active
  readiness/forced-end interception, delegates post-relaxation resume to
  `ScaleRuntime`, and stops writing Engine timeout markers to reports.

### Tests

- `tests/test_phase5_policy.py` — normative policy matrix for relaxation,
  game, explicit end, timeout, and Engine execution.
- `tests/test_phase5_policy_boundary.py` — static authority and no-second-
  policy checks.
- `tests/test_scale_pipeline_boundary.py` — verifies resume uses Runtime's
  actual next item.
- `tests/test_turn_authority_pipeline.py` — verifies explicit end does not
  enqueue a competing timeout check.
- Existing pipeline, Engine, FSM, and integration tests were updated only for
  the frozen Phase 5 semantics.

## Verification

- Full local regression:

  ```text
  440 passed, 0 failed, 0 skipped
  ```

  Command:
  `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe -m pytest tests -q`

- `git diff --check`: passed (exit code 0; Git emitted only its normal
  LF-to-CRLF working-copy warnings on Windows).
- Production-only static boundary scan: no `RouterProposalV2`, `TurnPolicyV2`,
  `ScaleRuntime.decide_action`, Phase 6/session-authority migration markers,
  or production imports of `safety/resources`.
- A100/vLLM endpoints, STT, TTS, VAD, and crisis-detachment architecture were
  not changed.

## Local runtime smoke

- Python/unit/integration smoke using the repository's available `.venv`: **RUN**
  via the full 440-test regression above.
- Real A100/vLLM 72B/3B serving, FunASR STT, VoxCPM2 TTS, VAD hardware, and
  end-to-end GPU media playback: **NOT RUN — environment unavailable**. No
  hardware/model pass is inferred from the local test result.

## Git result

- Local production commit: `95c8bb42134e9a7ff0eaa7a2d1fa6a812eb49fb8`.
- Implementation push and remote HEAD: **pending GitHub connectivity** at the
  time this record was created; finalize this field only after successful
  `git push` and `git fetch` verification.
- The final working-tree status must be clean after this record is committed;
  no unrelated files are to be staged.

## Phase boundary

This phase does not introduce `RouterProposalV2`, a second `TurnPolicy`, an
authoritative `TurnDecision` replacement, `ScaleRuntime` policy, a new
SessionEngine policy layer, ScaleRuntime migration, SessionEngine authority
migration, or any Phase 6 content. It does not reconnect crisis/Guard runtime
or modify A100/vLLM/STT/TTS deployment architecture. Work stops at Phase 5.
