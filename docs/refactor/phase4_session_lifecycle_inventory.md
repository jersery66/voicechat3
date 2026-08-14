# Phase 4 preflight: session-lifecycle ownership inventory

**Status:** pre-implementation inventory; production code is unchanged.

- Date: 2026-08-14
- Branch: `codex/a100-vllm-safety`
- Baseline HEAD: `0652d82da7f5ca78d762e24e60b257ca3ae906b9`
- Phase 3 implementation ancestor: `2967fc09344b3d6a4fc0395e299b4fba29b76e62`
- Python executable: `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe`
- Accepted baseline: `394 passed`, `0 failed`, `0 skipped`

This inventory records every mutable lifecycle copy found before Phase 4. It
does not authorize production edits. The Phase 4 goal is to make the engine
the one lifecycle writer while preserving `TurnPolicy` and `ScaleRuntime` as
their already-frozen authorities.

## Scope and search method

The read-only scan covered production Python files under `app/`, `assessment/`,
`conversation/`, `core/`, `game/`, `services/`, `ui/`, `config.py`, and
`main.py`, excluding `.venv`, `.git`, `__pycache__`, `tests/`, and generated
`graphify-out/` scripts. The scan looked for:

```text
_session_ending, _pending_end_after_video, _pending_quit,
_user_explicit_end, _pending_end_type, _pending_end_allow_force,
_pending_end_source, _timeout_dialog_open,
time_warning_shown, time_limit_prompt_shown,
continued_after_time_limit, _scale_interrupted_by_relaxation,
_resume_scale_after_relaxation, _post_relaxation_feedback_consumed,
_post_scale_relaxation_recommended, relaxation_active,
relaxation_completed, relaxation_used, exit_requested, finish_mode,
_game_candidate, _pending_relaxation_after_scale,
_relaxation_candidate, SessionEngine, SessionOrchestrator,
SessionEndController, SESSION_ENGINE_SHADOW,
SESSION_ENGINE_AUTHORITATIVE, current_relaxation_type,
has_forced_relaxation_rec, post_relaxation_timed_out
```

The inventory also follows direct `transition_to()`, `evaluate_session_end()`,
`reset()`, command forwarding, and lifecycle queue messages so that a field
whose name is not obviously a flag is not missed.

## Findings summary

1. `ui/main_window.py` constructs a legacy `SessionOrchestrator` and a legacy
   `SessionEndController`, mutates both directly, and owns a second set of end,
   timeout, playback, and resume flags.
2. `app/engine.py` constructs its own `SessionOrchestrator` and
   `SessionEndController`. It is currently a shadow mirror: it handles only a
   subset of commands while legacy UI logic remains authoritative.
3. `core/session_fsm.py` is the actual reducer used by both instances. Its
   transition rules and forced-relaxation logic are accepted behavior to
   preserve during the migration, not new Phase 4 policy.
4. `services/pipeline.py` correctly delegates questionnaire state to
   `assessment.ScaleRuntime`, but still holds several intervention candidates,
   session flags, and legacy compatibility fields. These must be classified as
   per-turn metadata, lifecycle state to delegate, or removable dead state;
   none may become a third lifecycle authority.
5. `services/report_service.py` owns duration/round/report facts and currently
   mutates one-shot timeout markers. Those markers affect lifecycle decisions
   and therefore need an engine-owned write path, while report metrics and
   persisted facts remain with the report service.
6. `conversation.contracts.TurnStateSnapshot` and `TurnSignals` are immutable
   turn inputs. Their `session_state`, `relaxation_used`, `game_active`, and
   `time_limit_reached` fields must be read from the engine/runtime boundary;
   they are not a replacement lifecycle store.

## State ownership matrix

| ID | State family and exact symbols | Current location / mutation sites | Current owner | Phase 4 target owner | Disposition and boundary |
|---|---|---|---|---|---|
| A1 | Primary lifecycle state: `SessionState`, `SessionContext.state` | `core/session_fsm.py:20-144`; `MainWindow.orchestrator` constructed at `ui/main_window.py:98`, reset/transitioned at `873`, `982-983`, `1021`, `1077`, `1112`, `1154-1155`, `1191`, `1221-1222`, `1657`, `1760`, `2005`, `2076`; engine copy at `app/engine.py:73`, handlers `220`, `264`, `271-302`, `318-377` | Two `SessionOrchestrator` instances; legacy UI is behaviorally authoritative | One engine-owned writer/reducer | Keep existing transition outcomes. Remove the MainWindow instance and direct transitions after command/event tests pass. The compatibility import in `services/session_orchestrator.py` may remain without a second instance. |
| A2 | Turn snapshot lifecycle projection: `TurnStateSnapshot.session_state`, `PipelineConfig.session_state`, `_session_state_provider` | `conversation/contracts.py:140-151`; `services/pipeline.py:357`, `376-387`, `449-455`, `468-479`; `ui/main_window.py:894` passes `lambda: self.orchestrator.state` | MainWindow orchestrator projection, sampled by Pipeline | Engine snapshot/event adapter; `TurnPolicy` receives an immutable value | Preserve Phase 2 `RouterProposal → TurnPolicy → TurnDecision`. The snapshot is read-only and never a state writer. |
| A3 | Shadow/authority switches: `SESSION_ENGINE_SHADOW`, `SESSION_ENGINE_AUTHORITATIVE` | `config.py:689-705`; `ui/main_window.py:101-123`, `_engine_submit()` at `985-998`; historical instructions in `docs/refactor/04_authority_switch.md` | Shadow mode is enabled by default; legacy remains authoritative; authoritative flag is unused | One non-selectable engine path | Remove the split switches only in the Phase 4 implementation commit after the real command/event path is green. This docs freeze does not change config. |
| B1 | End-in-progress guard: `_session_ending`, `is_ending`, `_ending` | UI writes at `63`, `956`, `1012`, `1652`, `1769`, `1788`, `1961-1966`, `2025`, `2177`; `core/end_guard.py:18-50`; engine reads `is_ending` at `206` and owns a separate guard at `74` | UI flag plus two guard instances | Engine snapshot/guard only | UI may retain a derived “report UI busy” indicator, but it cannot reject or start an end flow from a local flag. |
| B2 | Deferred end while playback: `_pending_end_after_video`, engine `_pending_end` | UI declaration `77`; writes `1541-1545`, `2021-2027`; consumed `1160-1170`; engine declaration `84`, writes/consumes `225`, `271-280`, `318-329` | Two independent pending-end records | Engine command queue and pending-end record | Preserve “finish video, then resume end” behavior. Delete the UI authoritative copy; video completion submits `RelaxationFinishedCommand`. |
| B3 | Pending end dialog context: `_pending_end_type`, `_pending_end_allow_force`, `_pending_end_source` | `ui/main_window.py:1576-1579`, read `1735-1746` | MainWindow end-dialog flow | Engine command payload plus immutable event/read model | Keep dialog rendering local; do not let a stale UI field determine the final end type or force flag. |
| B4 | Quit intent: `_pending_quit`, `ExitCommand` | UI declaration/reset `64`, `957`; writes `1474`, `1740`, `1751`; consumed `2031`; command exists in `app/contracts.py:133-136` but is currently rejected by engine `102-106`, `161-171` | MainWindow flag; engine does not own Exit | Engine end/exit intent; UI owns only wait-dialog handle | Preserve fast quit when no subject and report-first quit for an active session. `ExitCommand` must no longer disappear in the authoritative path. |
| B5 | Explicit end intent: `_user_explicit_end`, `EndSessionCommand.source/end_type/allow_force_relaxation` | UI writes `66`, `1250`, `1503`, `1713`, `1752`; consumed `1979-1980`; command forwarding `1949-1958` | MainWindow flag, copied into shadow command | Engine command/event input; `TurnDecision` remains the per-turn source when applicable | Treat explicit end as an input fact, not mutable session state. Preserve `QUIT`/`INVALID` no-force behavior. |
| B6 | End request/dialog gates: `_end_decision_open`, `_end_request_in_progress`, `_pre_end_relax_prompted`, `_auto_ending_after_relaxation` | Declarations `74-76`; resets `959-961`; dialog gates/mutations `1476`, `1513`, `1524`, `1591-1598`, `1625-1643`, `1653`, `2026` | Mostly MainWindow UI flow; `_pre_end_relax_prompted` also suppresses repeated recommendation | Engine lifecycle request state for decisions; UI dialog-open/request-busy values remain render-only | Move only state that affects whether a transition is accepted. Do not change recommendation criteria; Phase 5 owns policy changes. `_auto_ending_after_relaxation` is currently only initialized/reset and is removable after tests prove it is dead. |
| B7 | Report generation/farewell progress: `_current_report_generated`, `_current_report_generating`, `_completion_status`, `_exit_wait_dialog` | UI declarations `67-71`; report flags writes `1722-1732`, `1764-1771`, `2080`, `2159`, `2169`, `2177`; dialog lifecycle `1826-1860` | UI/report worker coordination | Engine owns lifecycle `SESSION_ENDING/SESSION_ENDED`; report worker emits completion; dialog handles stay UI-only | Keep report/PDF ordering and failure status. Do not make report generation itself a second state machine. |
| C1 | Relaxation/game lifecycle context: `current_relaxation_type`, `has_forced_relaxation_rec`, `post_relaxation_timed_out`, `relaxation_used` in `SessionContext` | `core/session_fsm.py:35-40`; engine checks/mutates `255-264`, `302`; UI writes `1143`, reads `1384`, `1698-1700`, `1905`; pipeline has a separate `relaxation_used` at `406-409`, `1180` | Engine FSM context plus MainWindow and Pipeline copies | Engine lifecycle snapshot; completed intervention facts flow to report service | Preserve current forced-relaxation and “at most once” outcomes. `ScaleRuntime` remains unrelated to intervention lifecycle. |
| C2 | Scale interruption/resume around relaxation: `_scale_interrupted_by_relaxation`, `_resume_scale_after_relaxation`, `_post_relaxation_feedback_consumed`, `_post_scale_relaxation_recommended` | UI declarations `84-87`; writes/reads `443-478`, `965-968`, `1103-1110`, `1173-1181`, `1548-1569`; actual scale restore calls `463-465`, `1552-1560` | MainWindow shadow copy; `ScaleRuntime` is the Phase 3 questionnaire owner | Engine sends pause/resume lifecycle commands; `ScaleRuntime` remains the only item/answer/pause owner | Remove stale UI item snapshots. A resume command must ask Runtime for its actual next item. Do not redesign the post-relaxation product rule in Phase 4. |
| C3 | Playback request/type: `_pending_relaxation_type`, `PlayRelaxationCommand`, `RelaxationFinishedCommand` | UI writes `1116`, consumes callback `1129-1155`; command forwarding `1096-1101`, `1133-1137`; engine handlers `292-329` | UI starts media and engine mirrors it | Engine owns playback phase; video service owns media execution; UI displays events | Failed playback remains incomplete and must not be recorded as completed. |
| C4 | Pipeline intervention/session flags: `_post_scale_relaxation_done`, `_relaxation_recommended_this_session`, `_game_recommended_this_session`, `_pending_relaxation_after_scale` | `services/pipeline.py:398-401`, reset `442-445`, decision writes `551-555`, reads/writes `997`, `1271-1278`, `1670` | Pipeline compatibility state and per-turn policy signals | Engine lifecycle snapshot or explicit per-turn/event metadata; remove dead copies | Keep `TurnPolicy` as action authority. Conditions for recommending relaxation/game are Phase 5; Phase 4 only removes ownership duplication. |
| C5 | Pipeline per-turn candidates: `_relaxation_candidate`, `_game_candidate`, `relaxation_rec`, `game_recommended` | `services/pipeline.py:402-403`, `551-555`, `1046`, `1177-1192`, `1368`; `PipelineResult` fields at `328`, `331` | Pipeline execution-local metadata derived from `TurnDecision` | Immutable turn result/event; no session authority | These are not lifecycle state. Keep only as short-lived metadata needed to render one decision, then discard. |
| C6 | Pipeline compatibility flags: `relaxation_recommended`, `relaxation_active`, `relaxation_completed`, `relaxation_used`, `exit_requested`, `finish_mode` | `services/pipeline.py:406-412`, reset `436-441`; `relaxation_used` read by `TurnStateSnapshot` at `476` | Pipeline fields, most are reset-only or legacy | Remove authoritative writes; feed engine snapshot/turn observations explicitly | Do not copy these into a larger engine shadow. A targeted test must prove unused flags can be deleted or are derived without behavior change. |
| C7 | Pipeline result lifecycle metadata: `PipelineResult.scale_active`, `scale_completed`, `all_scales_completed`, `completed_scale_name`, `end_type` | `services/pipeline.py:327-341`, writes `557-561`, `1164-1185`, `1261-1279`, `1406`; UI consumes result in `_post_pipeline_routing()` `565-595` | Pipeline result, then MainWindow queue | Immutable result/event adapter; engine accepts only lifecycle commands | Keep result/report metadata, but do not treat a result field as permission to mutate lifecycle directly on a worker thread. |
| D1 | Timeout one-shot markers: `time_warning_shown`, `time_limit_prompt_shown`, `continued_after_time_limit` | `services/report_service.py:53-57`, reset `152-161`, writes `214-229`; UI mirrors `1267-1269`; engine has duplicate private markers `77-81`, methods `should_emit_time_warning()` `340-371` | ReportService plus engine shadow copy | Engine single-writer decision markers; ReportService receives event/derived mirror for report compatibility | Preserve exactly-once warning/ask and continue suppression. Do not alter timeout copy or thresholds; that is Phase 5. |
| D2 | Session metrics and report facts: `session_start_time`, `round_count`, `completed_relaxation`, `game_results`, `activity_log` | `services/report_service.py:53-60`, lifecycle reset `152-171`, round increment `173-186`; UI reads progress/report at `1887-1925` | ReportService/data/report layer | Remain report/data owner; engine only signals lifecycle boundaries | These are not lifecycle authority. They may be updated from engine events and must not transition the session. |
| E1 | Per-turn lifecycle observations: `TurnStateSnapshot.relaxation_used`, `game_active`, `time_limit_reached`; `TurnSignals.legacy_*`; `PolicyDecision.exit_requested` | `conversation/contracts.py:140-163`, `232-279`; constructed from Pipeline at `468-479`, `997`; policy consumes at `65-148` | Pipeline/turn boundary | Immutable engine/runtime snapshot plus one `TurnDecision` | Preserve Phase 2 contracts. `exit_requested` is a turn input, not the engine's pending-quit store; `legacy_*` names are compatibility observations only. |
| E2 | Scale authority boundary: `ScaleRuntimeSnapshot` (`active_scale`, `current_item`, `waiting_for_answer`, answers, completed, pause/resume) | `assessment/scale_runtime.py` is the sole mutable owner; Pipeline reads `468-475`, UI calls Runtime adapters at `463-465`, `1552-1560` | `ScaleRuntime` | Remains `ScaleRuntime`; engine consumes read-only projection only | Phase 4 must not add an engine-side scale copy or `ScaleRuntime.decide_action()`. |
| F1 | Game subsystem internal state | `game/engine.py:36-60` and its tutorial/playing/ending/game-over transitions | `GameEngine` for the game itself; MainWindow only launches it | Remain game-owned; SessionEngine owns only session-level `GAME_PLAYING` projection | No game-rule or internal state migration in Phase 4. |
| F2 | Composition and compatibility shims | `main.py` creates `MainWindow` only; `services/session_orchestrator.py` and `services/session_end_controller.py` re-export core classes; MainWindow constructs both legacy services at `98-99` and shadow engine at `105-120` | MainWindow composition plus compatibility shims | Composition root creates/injects one engine; shims remain import-only if needed | No second reducer/controller may be constructed in production. `main.py` remains free of business transitions. |

## Direct mutation map

The following direct writes are the high-risk ownership violations to cover in
Phase 4 tests:

```text
ui/main_window.py
  self.orchestrator.reset() / transition_to(...) / evaluate_session_end(...)
  self.session_end_controller.reset() / begin() / defer_for_relaxation()
  self._session_ending = ...
  self._pending_end_after_video = ...
  self._pending_quit = ...
  self._user_explicit_end = ...
  self._scale_interrupted_by_relaxation = ...
  self._resume_scale_after_relaxation = ...

services/pipeline.py
  self.relaxation_* = ...
  self.exit_requested = ...
  self.finish_mode = ...
  self._pending_relaxation_after_scale = ...
  self._relaxation_candidate / self._game_candidate = ...

services/report_service.py
  self.time_warning_shown = ...
  self.time_limit_prompt_shown = ...
  self.continued_after_time_limit = ...

app/engine.py
  self._orchestrator = SessionOrchestrator()
  self._guard = SessionEndController()
  self._pending_end = ...
  self._time_warning_sent / _time_limit_ask_sent / _time_limit_continue_chosen = ...
```

The Phase 4 implementation must reduce these to one authoritative lifecycle
write path, not merely add another forwarding layer around the same writes.

## Phase-boundary exclusions

The following are deliberately classified as later work and must not appear
as new production contracts in the Phase 4 implementation:

- new relaxation recommendation thresholds or recommendation ownership rules;
- new game eligibility or game completion rules;
- explicit-end bypass changes, “好多了” interpretation, or timeout dialog
  redesign;
- `RouterProposalV2`, `TurnPolicy` replacement, or an authoritative
  `TurnDecision` replacement;
- `ScaleRuntime` action policy or migration of `SessionEngine` authority into a
  future `SessionEngine`/`SessionFSM` replacement;
- bulk report schema redesign or A100/vLLM/STT/TTS changes.

## Inventory acceptance gate

This inventory is complete for the Phase 4 docs freeze when:

1. every exact field in the matrix has a current owner, mutation sites, target
   owner, and disposition;
2. the direct mutation map has a corresponding future boundary test;
3. `MainWindow`, `Pipeline`, `ReportService`, `SessionEngine`, the core FSM,
   contracts, config, and game adapter are all represented;
4. Phase 3's `ScaleRuntime` single-owner boundary is explicitly preserved;
5. the working tree contains no production changes and the accepted `394` test
   baseline is still reproducible.
