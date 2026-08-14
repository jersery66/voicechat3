# Make SessionEngine authoritative — Phase 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. The steps below are the implementation contract; this document itself is a design-freeze artifact.

**Goal:** Make one `SessionEngine` the single writer and authoritative owner of the whole session lifecycle while keeping `TurnPolicy`, `ScaleRuntime`, and the existing A100/vLLM/STT/TTS deployment contracts unchanged.

**Architecture:** UI callbacks become command senders and event renderers. `SessionEngine` receives lifecycle commands on one writer thread, applies the existing lifecycle transition rules once, and emits immutable state/events. `ScaleRuntime` remains the only owner of questionnaire state; the engine may consume its read-only snapshot but must not duplicate scale item, answer, pause, or completion state.

**Tech Stack:** Python 3, PySide6, Pydantic command/event contracts, the existing `core.session_fsm` transition rules, `ConversationPipeline`, `TurnPolicy`, and `assessment.ScaleRuntime`.

---

# VoiceChat3 Phase 4 formal implementation specification

## Status and freeze boundary

- Status: **formal specification and ownership inventory only; Phase 4 production implementation is not started**.
- Date: 2026-08-14.
- Branch: `codex/a100-vllm-safety`.
- Phase 3 accepted implementation: `2967fc09344b3d6a4fc0395e299b4fba29b76e62` (`refactor: migrate all scale state to scale runtime`).
- Current docs-only baseline: `0652d82da7f5ca78d762e24e60b257ca3ae906b9` (`docs: finalize phase 3 implementation record`).
- Accepted baseline: `394 passed`, `0 failed`, `0 skipped`.
- Baseline executable: `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe`.
- No production file, test file, deployment file, model setting, or runtime contract is changed by freezing this document.

This is the execution contract for the next phase. It is deliberately written
before implementation so the lifecycle owner, migration boundaries, and
non-goals can be reviewed independently of code changes.

## 1. Objective

Phase 4 removes the second lifecycle authority currently split between the
legacy `MainWindow` flow and the shadow `SessionEngine` flow. The target path
is:

```text
Qt callback / timer / pipeline result
             │
             ▼
      lifecycle Command
             │
             ▼
      SessionEngine (one writer)
             │  exactly one transition
             ▼
      immutable state snapshot + Event(s)
             │
             ▼
      MainWindow renders and starts I/O only
```

The engine owns **where the session is in its lifecycle**. It does not own
language generation, answer interpretation, scale item selection, report
formatting, or Qt widgets.

## 2. Frozen contracts from accepted phases

The following contracts are inputs to Phase 4 and must remain intact:

### 2.1 Phase 1 safety boundary

- Crisis/Guard production behavior remains detached under `safety/`.
- Production `main.py` reachability and `knowledge/rag_service.py` must not
  acquire `safety/resources` access.
- `EndType.SAFETY` remains only as the compatibility value already accepted in
  Phase 1; Phase 4 does not reintroduce a crisis flow.

### 2.2 Phase 2 turn authority

```text
RouterProposal → TurnPolicy → exactly one TurnDecision
```

`RouterProposal` is non-authoritative, `TurnPolicy` is the only per-turn
business policy, and `TurnDecision` is the only executable turn action. Phase
4 must not add a lifecycle decision inside `ScaleRuntime`, `Pipeline`, or the
engine that competes with `TurnPolicy` for a turn action.

### 2.3 Phase 3 scale authority

`assessment.ScaleRuntime` remains the only mutable owner of:

```text
active_scale, current_item, waiting_for_answer,
answers_by_scale, completed_scales, paused, resume_item
```

The engine may receive a `ScaleRuntimeSnapshot` or a scale progress event for
projection, but it must not keep a second mutable copy and must not add
`ScaleRuntime.decide_action()` (or an equivalent policy method).

### 2.4 Deployment and media contracts

The A100/vLLM endpoints, model allocation, STT, TTS, VAD, desktop launch, and
normal report/PDF ordering remain unchanged. Phase 4 is an ownership refactor,
not a deployment or model migration.

## 3. Phase 4 scope

The implementation must do all of the following:

1. Make exactly one `SessionEngine` instance the lifecycle writer for a
   running session.
2. Move lifecycle state currently written by `MainWindow`, the duplicate
   `SessionOrchestrator`, and the duplicate `SessionEndController` behind that
   writer.
3. Make `MainWindow` send commands and consume engine events instead of calling
   `transition_to()` or `evaluate_session_end()` directly.
4. Replace shadow-mode forwarding with the real command/event path and remove
   the `SESSION_ENGINE_SHADOW` / unused authoritative-switch split once the
   implementation tests prove the new path.
5. Preserve current transition outcomes, deferred-end behavior, one-shot time
   warnings, report-first exit ordering, and relaxation/game playback behavior.
6. Keep UI-only concerns (dialogs, widget state, recording controls, progress
   indicators, pipeline cancellation tokens) local to the UI, but prevent them
   from becoming lifecycle truth.

## 4. Explicit non-goals and forbidden changes

Phase 4 must not:

- change the priority or semantics of `RouterProposal`, `TurnPolicy`, or
  `TurnDecision`;
- add Router item/score control or a second turn-policy implementation;
- move scale answers, current item, waiting, pause, resume, or completion into
  `SessionEngine`;
- redefine when relaxation is recommended, when a game is allowed, whether an
  explicit end bypasses relaxation, or what the phrase “好多了” means;
- redesign timeout dialogue copy or introduce a new timeout business rule;
- migrate `SessionEngine` authority into a future `SessionEngine`/lifecycle
  replacement, `ScaleRuntime` policy, or a Phase 5 session-policy object;
- redesign the game subsystem's internal `GameEngine.state` values;
- reconnect crisis/Guard runtime or import `safety/resources` from production;
- change `app/engine.py` into a model client, a Qt object, or a report service;
- begin the Phase 5 contracts named below.

The phrase “make the engine authoritative” means **single ownership of
existing lifecycle state and transitions**. It does not authorize new product
rules.

## 5. Target lifecycle model

The semantic target states are:

```text
IDLE
CHATTING
SCALE_ACTIVE
RELAXATION_RECOMMENDED
RELAXATION_PLAYING
POST_RELAXATION
GAME_PLAYING
SESSION_ENDING
SESSION_ENDED
```

The current compatibility FSM uses `VIDEO_PLAYING` for both relaxation and
game playback and does not yet expose `SCALE_ACTIVE`. The implementation may
retain compatibility enum members while introducing a read-model mapping:

| Current compatibility state/context | Target semantic state | Ownership rule |
|---|---|---|
| `IDLE` | `IDLE` | Engine snapshot |
| `CHATTING` with no active scale | `CHATTING` | Engine snapshot; `ScaleRuntime` is empty or paused |
| `ScaleRuntimeSnapshot.active_scale` is set and a scale turn is active | `SCALE_ACTIVE` | Runtime owns scale details; Engine owns only the lifecycle projection |
| `RELAXATION_RECOMMENDED` | `RELAXATION_RECOMMENDED` | Engine owns recommendation-wait state |
| `VIDEO_PLAYING` with a relaxation kind | `RELAXATION_PLAYING` | Engine owns playback phase; video service owns media I/O |
| `POST_RELAXATION` | `POST_RELAXATION` | Engine owns post-playback phase |
| `VIDEO_PLAYING` with `game` | `GAME_PLAYING` | Engine owns session phase; game engine owns internal game state |
| `SESSION_ENDING` | `SESSION_ENDING` | Engine owns end/report lifecycle |
| `SESSION_ENDED` | `SESSION_ENDED` | Engine owns terminal state until next subject preparation |

No state label may be used to smuggle in a new recommendation or end rule.
State transitions remain those already covered by `core.session_fsm` until a
separate, reviewed policy change is approved.

## 6. Authority and data-flow invariants

### 6.1 Single writer

- Only the engine writer thread may mutate the authoritative lifecycle
  snapshot, end-defer record, relaxation/game phase, timeout one-shot markers,
  or terminal state.
- `MainWindow` must not call `SessionOrchestrator.transition_to()`,
  `evaluate_session_end()`, `SessionEndController.begin()`, or write a
  lifecycle flag as a substitute for a command.
- `ConversationPipeline` may return turn metadata and `TurnDecision`; it may
  not mutate engine lifecycle state directly from its worker thread.
- Timers, video callbacks, game callbacks, and dialogs submit commands; they do
  not write lifecycle state directly.

### 6.2 Read-only consumers

- `MainWindow` reads an immutable engine snapshot or event payload for button,
  dialog, and status rendering.
- `TurnPolicy` receives a read-only `TurnStateSnapshot`; it remains the sole
  per-turn action authority and does not receive a mutable engine object.
- `Pipeline` receives the session state as input and emits a result; it does not
  become a session state store.
- `ReportService` records duration, rounds, completed intervention facts, and
  report data. It no longer decides whether the session is ending.

### 6.3 Exactly-once transition/event rule

For each accepted lifecycle command, the engine performs at most one state
transition and emits a deterministic event sequence. Rejected or duplicate
commands emit an explicit warning/error event or a documented no-op; they must
not be silently consumed by a second state machine.

## 7. Existing commands and events to preserve or wire

`app/contracts.py` already contains the command/event boundary. Phase 4 should
reuse these contracts before adding new ones.

### Lifecycle commands

| Command | Phase 4 handling |
|---|---|
| `StartSessionCommand` | Reset engine lifecycle and enter `CHATTING`; clear pending end/timeout markers. |
| `EndSessionCommand` | Apply the existing end guard/forced-relaxation/deferred-video behavior once. |
| `PlayRelaxationCommand` | Enter relaxation playback only when the current FSM allows it. |
| `RelaxationFinishedCommand` | Close playback, preserve the existing post-relaxation event/choice flow, and resume a deferred end. |
| `ContinueChatCommand` | Leave `POST_RELAXATION` for chat without changing recommendation policy. |
| `PlayGameCommand` | Enter and leave the game lifecycle phase; game internals remain outside the engine. |
| `AcknowledgeTimeLimitCommand` | Consume the existing one-shot continue choice once. |
| `PrepareNextSubjectCommand` | Clear the engine session snapshot without starting the next participant. |
| `ExitCommand` | Represent fast quit versus report-first exit through the existing end contract; do not let UI flags decide it. |

`UserTextCommand`, `StartRecordingCommand`, `StopRecordingCommand`,
`SelectMediaCommand`, and `ConfirmUserInfoCommand` remain adapter/input
commands. They must have an explicit owner/response (conversation, voice,
media, or form adapter) and must not be silently dropped by a supposedly
authoritative engine.

### Events

The existing `StateChangedEvent`, `RelaxationRecommendedEvent`,
`ContinueOrEndAskEvent`, `SessionWarningEvent`, `TimeLimitAskEvent`,
`SessionEndingEvent`, `SessionEndedEvent`, `ScaleProgressEvent`, `StatusEvent`,
and `ErrorEvent` are the preferred event surface. New event types are allowed
only when an existing event cannot carry a read-only lifecycle fact; a new
event must not carry a second mutable state model.

## 8. Time, end, relaxation, and game compatibility rules

The following are **preserved behavior**, not new Phase 4 decisions:

- forced relaxation before a normal goal end remains governed by the current
  `SessionOrchestrator.evaluate_session_end()` semantics;
- `QUIT` and `INVALID` remain no-force end types;
- an end requested during playback remains deferred until playback completion;
- duplicate end requests remain idempotently rejected;
- the time warning and time-limit ask remain single-shot, and a continue choice
  suppresses subsequent asks for that session;
- report/PDF persistence completes before farewell TTS, and a quit path keeps
  its existing no-farewell behavior;
- a failed video/game is not recorded as completed intervention;
- an interrupted scale is resumed through `ScaleRuntime`'s actual snapshot,
  not a stale UI item hint.

The engine owns the markers needed to enforce these existing outcomes. The
implementation may keep compatibility mirrors in `ReportService` or a UI
adapter only while tests prove they cannot influence a transition.

## 9. Implementation work breakdown (future production phase)

Each task is intentionally small and testable. No task below is executed by
this docs-freeze commit.

### Task 1: Lock lifecycle ownership tests

**Files:**

- Create: `tests/test_phase4_lifecycle_boundary.py`
- Modify: `tests/test_app_engine.py` only where an existing contract must be
  asserted more precisely.

- [ ] Write failing static tests that identify every production
  `SessionOrchestrator`/`SessionEndController` construction and every direct
  `MainWindow.orchestrator.transition_to()`/`evaluate_session_end()` call.
- [ ] Write failing behavioral tests that a command produces one state event,
  that duplicate end requests are idempotent, and that unsupported commands
  produce an explicit event rather than disappearing.
- [ ] Run `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe -m pytest tests/test_phase4_lifecycle_boundary.py tests/test_app_engine.py -q`; record the expected red state before production edits.

### Task 2: Define the engine snapshot and single-writer adapter

**Files:**

- Modify: `app/engine.py`
- Modify: `app/contracts.py` only for an immutable read model or event that is
  proven necessary by Task 1.
- Test: `tests/test_app_engine.py`, `tests/test_phase4_lifecycle_boundary.py`

- [ ] Put all lifecycle-mutating fields behind the engine writer and expose a
  defensive snapshot; keep `ScaleRuntimeSnapshot` separate and read-only.
- [ ] Route existing `core.session_fsm` transition rules through this one
  engine-owned instance; preserve the current state outcomes and no-force end
  types.
- [ ] Make all handled lifecycle commands and deferred-end paths pass the
  single-writer tests.
- [ ] Run the targeted engine/boundary tests and require green before UI work.

### Task 3: Convert MainWindow to command sender/event consumer

**Files:**

- Modify: `ui/main_window.py`
- Modify: `main.py` only if composition-root injection is required to create
  exactly one engine.
- Test: `tests/integration/test_ui_boot_headless.py`,
  `tests/test_conversation_integration.py`,
  `tests/test_phase4_lifecycle_boundary.py`

- [ ] Replace each direct lifecycle mutation with the corresponding command.
- [ ] Subscribe one UI event bridge to engine events and keep widget/dialog
  changes on the Qt thread.
- [ ] Keep `_pipeline_busy`, generation tokens, dialog handles, and recording
  controls as UI-only synchronization; they cannot gate the engine state.
- [ ] Run the offscreen UI tests with `QT_QPA_PLATFORM=offscreen` and verify
  start, relaxation, deferred end, timeout ask, report completion, and quit
  parity.

### Task 4: Remove shadow mode and duplicate lifecycle holders

**Files:**

- Modify: `config.py`
- Modify: `ui/main_window.py`
- Modify: `services/session_orchestrator.py` and/or `core/session_fsm.py` only
  to leave one engine-owned reducer and a documented compatibility import.
- Modify: `services/session_end_controller.py` and/or `core/end_guard.py` only
  to leave one engine-owned guard.
- Modify: `docs/refactor/04_authority_switch.md` to describe the completed
  switch after implementation passes.
- Test: all lifecycle boundary and headless UI tests.

- [ ] Remove the active shadow branch and the unused authoritative flag after
  the command/event path is green; do not leave two code paths selectable at
  runtime.
- [ ] Prove with static tests that production constructs one engine/reducer and
  no MainWindow direct transition remains.
- [ ] Run the complete Phase 1/2/3 boundary suites before final review.

### Task 5: Final regression and acceptance

**Files:**

- Modify: `docs/refactor/phase4_session_engine_implementation.md` (created
  only after production implementation is complete).

- [ ] Run the affected lifecycle, pipeline, ScaleRuntime, turn-authority,
  report, and UI suites.
- [ ] Run `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe -m pytest tests -q`.
- [ ] Run `git diff --check`, inspect `git status --short`, and verify no
  Phase 5 contract or production file slipped into the Phase 4 commit.
- [ ] Only after all gates pass, create the independent implementation commit
  `refactor: make session engine authoritative`.

## 10. Acceptance gates for this specification freeze

This docs-only phase is accepted when all of the following are true:

1. `phase4_session_engine_spec.md` and
   `phase4_session_lifecycle_inventory.md` are internally consistent.
2. The inventory covers every lifecycle shadow field and mutation family in
   `MainWindow`, `ConversationPipeline`, `ReportService`, `SessionEngine`,
   `SessionOrchestrator`, `SessionEndController`, `app/contracts.py`,
   `config.py`, and the relevant UI/game adapters.
3. Production code has no diff; only the two Phase 4 docs are staged.
4. The accepted baseline remains `394 passed`, `0 failed`, `0 skipped`.
5. No `RouterProposalV2`, `TurnPolicy` replacement, authoritative
   `TurnDecision` replacement, `ScaleRuntime` action policy, SessionEngine
   implementation, or Phase 5 lifecycle-policy rule appears in this commit.
6. The next production commit is reserved for
   `refactor: make session engine authoritative`; Phase 5 is not started.
