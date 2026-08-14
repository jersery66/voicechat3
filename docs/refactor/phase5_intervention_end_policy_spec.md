# Phase 5: unify intervention, game, end, and timeout policy

> This document is the Phase 5 design-freeze artifact. It is an execution
> contract for the next production phase, not a production change.

## Status and freeze boundary

- Status: **formal specification only; Phase 5 production implementation is
  not started**.
- Date: 2026-08-14.
- Branch: `codex/a100-vllm-safety`.
- Phase 4 implementation baseline:
  `aa1a0416fc53cb0ce605be8da648ce40a0f058e1` (`refactor: make session engine
  authoritative`).
- Current docs baseline:
  `2d650b3728020fb50b1ca402502188fd651066d8`.
- Accepted regression baseline: `405 passed`, `0 failed`, `0 skipped`.
- Python executable used for the local baseline:
  `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe`.
- Freezing this specification changes no production file, test, deployment
  setting, model endpoint, STT/TTS configuration, or runtime contract.

The inventory in `phase5_policy_inventory.md` is part of this freeze. The two
documents must be reviewed together before any production edit.

## 1. Objective

Phase 5 makes the conditions for relaxation, game, explicit end, and timeout
consistent. It does **not** introduce another state owner. The accepted
control chain remains:

```text
RouterProposal (suggestion)
        -> TurnPolicy (one policy decision)
        -> exactly one TurnDecision
        -> SessionEngine / ScaleRuntime execution
        -> MainWindow event rendering and device I/O
```

The division of responsibility is frozen as follows:

| Component | Authority in Phase 5 |
|---|---|
| `RouterProposal` / 3B Router | Supplies observations and a suggested action; cannot start media, end a session, choose a scale item, or choose a score. |
| `TurnPolicy` | The only per-turn business-policy authority. It approves or rejects relaxation, game, and end actions from immutable signals and the read-only snapshot. |
| `ScaleRuntime` | The only owner of questionnaire progress. It does not decide intervention, game, end, or timeout policy. |
| `SessionEngine` | Executes an already approved lifecycle command, owns lifecycle transitions and one-shot timeout markers, and emits events. It does not infer intent or invent eligibility rules. |
| `MainWindow` | Sends commands, renders decisions/events, and controls Qt/media/recording I/O. It cannot use a readiness check or local flag as a second policy. |
| `ReportService` | Stores duration, round, completed-intervention, and report facts. It does not decide whether an action is approved. |
| 72B dialogue model | Produces participant-facing language only. Its `[END_*]` and `[REC_*]` tags are metadata and never authorize an action. |

## 2. Frozen invariants from Phases 1–4

1. Crisis/Guard runtime remains detached under `safety/`; this phase must not
   reconnect it or import `safety/resources` from production.
2. `RouterProposal -> TurnPolicy -> exactly one TurnDecision` remains the
   only turn-decision path. No `SessionEngine.decide_*`,
   `ScaleRuntime.decide_action()`, or UI policy shortcut may be added.
3. `ScaleRuntime` remains the sole mutable owner of
   `active_scale`, `current_item`, `waiting_for_answer`, per-scale answers,
   completion, pause, and resume state.
4. SessionEngine remains the single lifecycle writer established in Phase 4.
   Phase 5 changes which commands are approved, not who executes them.
5. A Router or dialogue response may be wrong, missing, duplicated, or
   decorated with legacy fields without bypassing the policy boundary.

## 3. Frozen Phase 5 policy rules

The following are normative. “Signal” means an observed fact; “decision” means
the only executable action returned by `TurnPolicy`.

### 3.1 Relaxation

| Situation | Required policy result | Execution boundary |
|---|---|---|
| The user explicitly asks for a relaxation exercise/media | Approve `RECOMMEND_RELAXATION` without a minimum-round gate, unless an existing lifecycle rejection applies. | SessionEngine receives the approved command; ScaleRuntime is paused through its existing command path when necessary. |
| The system proposes relaxation without an explicit user request | Approve only when `round_count >= MIN_ROUNDS_FOR_RELAXATION` (currently 8), no proactive recommendation has already been made in this session, and the runtime is not waiting for a scale answer. | UI highlights/starts media only after the final `TurnDecision`; Router/LLM tags alone cannot start it. |
| A scale is waiting for an answer | Reject a **proactive** relaxation recommendation and keep the current Runtime item unchanged. | A user-requested interruption may be represented separately and must resume the actual Runtime snapshot after playback. |
| A proactive recommendation was already offered | Reject another proactive recommendation for the same session. | The one-shot fact is a policy input, not a UI button flag. |
| Relaxation completes | Preserve the completion fact for reports and emit the normal post-relaxation lifecycle event. | If a scale was paused, resume its real `ScaleRuntime` item; do not restore a UI-cached question number. |

The implementation must distinguish `user_requested_relaxation` from
`proactive_relaxation_candidate`. A single `relaxation_used`/completed flag
must not silently serve both meanings.

### 3.2 Game

- A game may start only after a clear user request (for example, “想玩个
  游戏” or an equivalent explicit request signal) has been observed and
  approved by `TurnPolicy`.
- “无聊”, “没意思”, low mood, or an entertainment intent by itself is not
  an authorization to start a game. It remains chat/media context unless the
  user asks to play.
- `RouterAction.RECOMMEND_GAME` is still a proposal vocabulary item, but
  `TurnPolicy` must cross-check the explicit-request signal before returning
  `TurnAction.RECOMMEND_GAME`.
- SessionEngine only executes `PlayGameCommand` after that decision. Game
  internals remain owned by `GameEngine`.

### 3.3 Explicit end

- Deterministic user phrases such as “不想聊了”, “结束”, “退出”, “今天先
  这样” are input signals. The current detector in `core/scoring.py` is the
  starting point and must retain its weak-response exclusions.
- An explicit user end produces exactly one
  `TurnDecision(action=END_SESSION, end_reason="user_explicit")`.
- Explicit end does not complete unfinished scales, force relaxation, ask a
  readiness dialog, or wait for a user to accept a compensating intervention.
- “好多了”, “轻松了”, “舒服点了”, or similar post-relaxation feedback is
  not an end signal and must remain ordinary conversation/post-relaxation
  input.
- A request received while media is playing may be physically deferred by
  SessionEngine until the media callback is safe, but the decision must not be
  converted into a forced relaxation or a second readiness policy.
- `RouterProposal` end suggestions and 72B `[END_*]` tags are lower-authority
  observations. They cannot override the explicit-end signal, invent one, or
  end the session when `TurnPolicy` returned chat/scale.

### 3.4 Timeout

- The soft warning and hard-limit ask are deterministic system signals; no
  language model decides whether the timeout was reached.
- Each session emits the continue/end question at most once.
- Choosing **continue** records the one-shot acknowledgement and suppresses
  every later timeout question for that session.
- Reaching the limit only opens the choice; it does not silently force
  `END_SESSION`. Choosing **end** sends an explicit lifecycle command.
- The implementation must have one owner for the one-shot markers. The
  current duplicate `ReportService`/`SessionEngine` markers are inventory
  findings to reconcile, not two accepted policies.

### 3.5 Post-relaxation and scale resume

- Completing relaxation/game enters the existing post-intervention lifecycle
  event. It must not infer an end from a positive feedback phrase.
- If `ScaleRuntime` was paused, the next question comes from
  `ScaleRuntime.snapshot()`/its resume operation. UI `_scale_tags`, cached item
  numbers, and old pipeline hints cannot be authoritative.
- A failed or cancelled media run is not recorded as a completed relaxation.
- Phase 5 does not redesign the media player, the game state machine, or the
  Phase 4 report-first ordering.

## 4. Signal, decision, and execution contract

The implementation must make the following flow observable in tests:

```text
user text / timer / runtime snapshot
       -> pure signals
       -> TurnPolicy.decide(...)
       -> exactly one immutable TurnDecision
       -> command adapter
       -> SessionEngine or ScaleRuntime
       -> event
       -> MainWindow rendering / media I/O
```

Required signal distinctions include:

- explicit end request versus weak acknowledgement;
- explicit relaxation request versus proactive recommendation candidate;
- explicit game request versus boredom/entertainment context;
- timeout reached versus user choice to continue/end;
- paused Runtime state versus a stale UI scale projection.

Signal collection may use deterministic keyword checks and the Router as
observations, but it must not mutate state or call a model in a way that
executes an action. Natural-language dialogue generation remains outside
`TurnPolicy`.

## 5. Current violations to resolve during implementation

The inventory records the evidence in detail. The implementation must at
least address these conflicts:

1. `conversation/turn_policy.py` currently accepts a Router relaxation
   proposal without the eight-round proactive gate and accepts a legacy
   relaxation/game candidate directly.
2. `services/pipeline.py` still carries `_pending_relaxation_after_scale`,
   `_relaxation_candidate`, `_game_candidate`, and per-session recommendation
   flags that can act as policy state.
3. `services/agent_service.py` and `config.py` contain entertainment/boredom
   keyword fallbacks and a Router prompt that says “无聊” can recommend a
   game. These must become explicit-request signals or non-authoritative chat
   observations.
4. `ui/main_window.py::_request_end_with_readiness_check()` currently prompts
   about incomplete scales and recommends relaxation before many end paths.
   Explicit user end must bypass those policy gates.
5. `core/session_fsm.py`/`app/engine.py` still implement forced-relaxation
   interception for normal ends. Phase 5 must keep execution in the engine,
   but the force decision must come from the approved policy contract rather
   than an engine-side rule.
6. `services/report_service.py` and `services/pipeline.py` can both emit or
   infer timeout behavior. The one-shot question must have one authoritative
   path.
7. `core/tags.py` and the pipeline still parse `[END_*]`/`[REC_*]`; these
   parsers may remain for cleaning/report metadata, but a raw tag must never
   create a decision.
8. `PolicyDecision` and `relaxation_tool.py` are compatibility/secondary
   paths. They must not become a competing policy authority while being
   retired or narrowed.

## 6. Implementation sequence (future production work)

No task in this section is executed by the docs-freeze commit.

### Task 1 — policy boundary tests and contract audit

- Add focused tests for the rule matrix before changing production behavior.
- Cover explicit end, weak responses, positive feedback, user/proactive
  relaxation, boredom/explicit game request, timeout one-shot behavior, and
  post-relaxation Runtime resume.
- Add static tests proving that 72B tags, Router item/score fields, and UI
  readiness flags cannot authorize a decision.
- Run the affected tests and record the expected red baseline.

### Task 2 — explicit end and timeout routing

- Route deterministic explicit-end signals through `TurnPolicy` and one
  `TurnDecision`.
- Remove readiness/forced-relaxation interception from explicit user end
  paths while preserving report-first execution and playback-safe deferral.
- Reconcile timeout marker ownership and retain one-shot continue semantics.

### Task 3 — relaxation eligibility

- Separate explicit user relaxation requests from proactive candidates.
- Enforce the eight-round/minimum-one-proactive recommendation rules in the
  policy boundary, not in UI, Pipeline, or Engine execution code.
- Keep Runtime pause/resume and completed-intervention reporting intact.

### Task 4 — game eligibility

- Require an explicit game request signal.
- Treat boredom/entertainment classification as context only unless the user
  asks to play.
- Keep `GameEngine` and media execution unchanged.

### Task 5 — post-relaxation and compatibility cleanup

- Resume the actual `ScaleRuntime` snapshot after an interruption.
- Narrow or remove legacy candidates, the secondary relaxation tool, and
  `PolicyDecision` only after compatibility tests prove they are no longer
  required.
- Keep report facts and participant-facing language adapters separate from
  policy decisions.

### Task 6 — regression and final record

- Run all affected policy, pipeline, engine, UI, report, scale, and tag tests.
- Run the complete suite with the available local environment:
  `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe -m pytest tests -q`.
- Run `git diff --check`, inspect status/stat/diff, and verify that no Phase 6
  authority migration or unrelated deployment change slipped in.
- Only after all gates pass, create the independent implementation commit:
  `refactor: unify relaxation game end and timeout routing`.

## 7. Acceptance gates for Phase 5 design freeze

The specification is frozen only when:

1. This document and `phase5_policy_inventory.md` agree on every rule and
   owner/disposition.
2. Every legacy rule named by the user is present in the inventory with a
   current location, current decision owner, execution owner, and target
   disposition.
3. The accepted Phase 4 baseline remains `405 passed`, `0 failed`, `0 skipped`.
4. The worktree contains no Phase 5 production or test edits as a consequence
   of this freeze.
5. The documents explicitly forbid `RouterProposalV2`, a second
   `TurnPolicy`, `ScaleRuntime` policy, a new SessionEngine policy layer,
   another session-authority migration, safety reconnection, and A100/vLLM/
   STT/TTS changes.
6. The next production commit is reserved for
   `refactor: unify relaxation game end and timeout routing`; Phase 6 is not
   started.
