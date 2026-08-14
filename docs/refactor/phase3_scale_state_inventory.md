# Phase 3 preflight: scale-state ownership inventory

**Status:** pre-implementation inventory; production code is unchanged.

- Date: 2026-08-14
- Branch: `codex/a100-vllm-safety`
- Phase 2 baseline HEAD: `078d186ff42360e4a1f04d3b84315fd852f46f66`
- Phase 2 implementation ancestor: `3fa975b97217e4a7ce3f23f6d2ffabf211885349`
- Python executable: `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe`
- Baseline command: `& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests -q`
**Baseline result:** `351 passed in 65.32s`, `0 failed`, `0 skipped` (exit code `0`)

This document records where scale state is actually stored and mutated before
Phase 3. It is not an implementation approval for the migration. The branch
must remain at the Phase 2 runtime contract until the formal Phase 3 tasks are
started.

## Scope lock

Phase 3 means **scale administration state ownership**:

```text
RouterProposal -> TurnPolicy -> exactly one TurnDecision -> Pipeline executor -> ScaleRuntime
```

`ScaleRuntime` will own the active assessment state and deterministic
transitions. It will not decide the business action, call a model, interpret
natural language, render a prompt, or become a second `TurnPolicy`.

The following are explicitly frozen and are not part of this preflight:

- Phase 2 `RouterProposal`, `TurnStateSnapshot`, `TurnSignals`,
  `TurnPolicy`, and `TurnDecision` authority.
- Router item/score fields: `requested_item`, `item`, `target_item`,
  `scale_score`, and equivalent fields must not return to Router JSON.
- SessionEngine/SessionFSM authority migration (Phase 4/5 work).
- Relaxation/game lifecycle redesign.
- Crisis/Guard runtime changes or re-connection of `safety/**`.
- A100/vLLM endpoints, model allocation, STT, TTS, or desktop deployment.

Symptom keyword counters are observations used to produce a
`TurnSignals.deterministic_scale_candidate`; they are not questionnaire
answers. They may remain in the signal-observation layer during Phase 3, but
they must never become a second source of active item, waiting, answer, or
completion state.

## Findings

1. `assessment/scale_runtime.py` is currently a thin, single-scale holder. It
   has `active_scale`, one `current_item`, and one `answers` dictionary, but it
   is not connected to the production pipeline.
2. `core.scale_fsm.ScaleState` is the current mutable value container for the
   production pipeline. `services/pipeline.py` owns the transitions through
   roughly two dozen delegated properties and direct mutation sites.
3. The pipeline's `ScaleState` has more than one representation of similar
   facts: `active_scale`/`scale_name`, `active_item`/`scale_current_item`,
   `waiting_answer`, `scale_completed`, administered sets, and derived
   completion checks.
4. Answer acceptance is currently interleaved with LLM tag parsing, short
   answer heuristics, symptom inference, and advancement. A tag or heuristic
   can write `_scale_answers` before a scale-definition validator is consulted.
5. The current `ScaleRuntime.record_answer()` accepts the hard-coded set
   `{0, 1, 2, 3, 4}`. That is incorrect for PHQ-9 and GAD-7, whose legal
   scores are `0..3`.
6. `services/scales.py:SCALES` already contains the authoritative question,
   option, item-count, maximum-score, and severity definitions. The runtime
   must consume this registry rather than duplicate score ranges or counts.
7. `ui/main_window.py` keeps resume and interruption copies, and aggregates
   `PipelineResult.scale_tags` into `_scale_tags`. Those values must become
   derived/reporting metadata after the migration, not a business-state
   source.
8. Phase 2 already makes the Router proposal non-authoritative. The current
   inventory therefore treats any item/score hint as discarded input, not as a
   migration requirement.

## State ownership matrix

| State or observation | Current value source | Current transition/mutation sites | Phase 3 target owner | Disposition |
|---|---|---|---|---|
| Active scale | `ScaleState.active_scale`, exposed as `_active_scale` | `ConversationPipeline._apply_turn_decision`, `restore_active_scale`, `force_resume_incomplete_scale`, `_advance_active_scale_after_score` | `ScaleRuntime.active_scale` | Remove Pipeline shadow aliases after delegation tests are green. |
| Current item | `ScaleState.active_item` plus `scale_current_item` compatibility field | Pipeline start, resume, score advancement, prompt builders | `ScaleRuntime.current_item` | One item only; Router never supplies it. |
| Waiting for answer | `ScaleState.waiting_answer` / `_active_scale_waiting_answer` | Pipeline answer fallback and pause/resume branches | `ScaleRuntime.waiting_for_answer` | Ambiguous/refusal keeps the current item and waiting state. |
| Answers by scale | `ScaleState.answers` / `_scale_answers` | LLM tag loop, short-answer scoring, text inference, `_record_scale_score` | `ScaleRuntime.answers_by_scale` | Only an accepted, definition-validated score may mutate this map. |
| Administered scales | `ScaleState.administered` / `_administered_scales` | Start and score paths | `ScaleRuntime.administered_scales` or a derived set | Must not be maintained independently by Pipeline. |
| Completed scales | `scale_completed` plus `_completed_scale_names()` derived from answers | `_advance_active_scale_after_score`, snapshot construction, report helpers | `ScaleRuntime.completed_scales` | Completion is explicit and derived results must agree with it. |
| Pause state | `soft_paused`, `pause_turns`, `resume_item` | `_soft_pause_scale`, `CONTINUE_SCALE`, `restore_active_scale`, UI relaxation callbacks | `ScaleRuntime.paused`, `resume_item` (with compatibility pause metadata if needed) | Preserve the actual unanswered item; remove UI-owned resume state. |
| Scale queue | `ScaleState.queue` / `_scale_queue` | Pipeline start/completion cleanup | Runtime assessment metadata or a removed compatibility field | No cross-scale switching while an active scale exists. |
| Refusal/defer/ask counters | `scale_refused_rounds`, `scale_defer_until_round`, `last_scale_ask_round`, `consecutive_scale_asks` | Pipeline flow bookkeeping | `ScaleRuntime` compatibility metadata, if still needed | Keep them non-authoritative; they may inform `TurnSignals` but cannot bypass `TurnPolicy`. |
| Symptom signal counters | `symptom_scores`, `symptom_turns`, `last_scale_trigger_round`, cooldown | Pipeline deterministic signal collection | Signal-observation layer feeding `TurnSignals` | Explicitly not questionnaire answers; do not use as Runtime state. |
| Current-turn tag metadata | `PipelineResult.scale_tags` | `parse_scale_tags()` and result assembly | Derived reporting/diagnostic metadata | Tags cannot create a scale, switch item, or overwrite Runtime state. |
| UI report aggregation | `MainWindow._scale_tags` | Result callback updates and report preparation | Runtime-derived report snapshot | Keep only as a compatibility/read model until report migration is complete. |
| Phase 2 policy snapshot | `TurnStateSnapshot.active_scale`, `current_item`, `waiting_for_answer`, `completed_scales` | Pipeline builds it from legacy fields | Built from `ScaleRuntime.snapshot()` | Preserve the contract; change only the source of its values. |

The key boundary is that symptom observations may inform a proposal/signal,
while `active_scale`, item progression, answer acceptance, pause/resume, and
completion are Runtime state. No field in the latter group may be written by
Pipeline, UI, Router, or the 72B model after migration.

## Completeness gate: A–I state families

The following gate explicitly covers the requested state families, including
ephemeral per-turn values and downstream report/data snapshots.

| Family | Current locations and symbols | Current owner | Phase 3 target/disposition |
|---|---|---|---|
| A. Current scale identity | `ScaleState.active_scale`, `scale_name`, Pipeline `_active_scale`; UI reads through Pipeline | `ScaleState` values, Pipeline transitions | `ScaleRuntime.active_scale`; all other copies become read-only projections. |
| B. Current item | `active_item`, `scale_current_item`, `_active_scale_q`, prompt builders | Pipeline/`ScaleState` | `ScaleRuntime.current_item`; Router and 72B item hints remain discarded. |
| C. Waiting for answer | `waiting_answer`, `_active_scale_waiting_answer`, `TurnStateSnapshot.waiting_for_answer` | Pipeline/`ScaleState` | Runtime owns mutable waiting; `TurnStateSnapshot` is derived. |
| D. Per-scale answers | `answers`, `_scale_answers`, response `scale_tags`, report-tool `scale_tags` input | Pipeline tag/inference writes | Runtime `answers_by_scale`; tags and report inputs become validated/derived data only. |
| E. Completed/administered | `administered`, `scale_completed`, `_completed_scale_names()`, `TurnStateSnapshot.completed_scales` | Pipeline plus derived answer checks | Runtime `administered_scales`/`completed_scales`; policy snapshot reads Runtime. |
| F. Pause/resume | `soft_paused`, `pause_turns`, `resume_item`, `pending_scale_resume`; UI `_scale_interrupted_by_relaxation`, `_resume_scale_after_relaxation` | Pipeline and MainWindow shadow flags | Runtime owns pause and actual resume item; UI flags are removed or reduced to transient view state. |
| G. Temporary score/clarification state | Pipeline locals `parsed_scale_tags`, `short_score`, `inferred`, `_pre_positive`, `_positive_pending`; `_make_scale_clarify_reply()` and prompt `system_suffix` | Per-turn Pipeline/LLM response path; no durable object | Pure answer-interpreter result plus prompt instruction. No pending score or clarification may survive as hidden Runtime state. |
| H. UI shadow state | `_scale_tags`, `_asking_scales`, `_scale_interrupted_by_relaxation`, `_resume_scale_after_relaxation`, `_post_scale_relaxation_recommended`, `_post_relaxation_feedback_consumed` | MainWindow | `_scale_tags` is derived reporting metadata; interruption flags are compatibility UI state only and cannot select/score/resume an item. |
| I. Report/DataManager snapshots | `Pipeline.get_scale_results()`, `report_tool._score_scales()`, `report_service` scale input, `TreatmentProgress.scale_scores`, `stats_service` latest/first scale reads; `DataManager` persists session artifacts but has no active-item API | Derived report/persistence consumers | Runtime-derived result snapshot is the only input; persistence and statistics never feed current-turn authority. |

### G-family clarification

There is no durable `pending_score` field in the current tree. `short_score`,
`inferred`, and parsed tag values are local candidates created during one
Pipeline turn. Likewise, clarification and positive-frequency follow-up are
currently represented by reply/prompt branches rather than a separate state
object. Phase 3 must make this explicit with an interpreter result and must
not turn these locals into a second mutable answer store.

## Production-file inventory

| Path | Current authority or copy | Relevant symbols/behavior | Phase 3 disposition |
|---|---|---|---|
| `assessment/scale_runtime.py` | Unused thin skeleton | `active_scale`, `current_item`, single-scale `answers`, `start`, `record_answer`, `next_item`; hard-coded scores `0..4` | Expand into the sole mutable runtime and add immutable snapshots, legal-score validation, pause/resume, completion, and reset. No model or policy imports. |
| `assessment/__init__.py` | Public assessment exports | Exports `ScaleRuntime`, `ScalePolicy`, `ScaleDirective` | Retain stable exports; add only read/answer value types needed by the Runtime contract. |
| `assessment/scale_policy.py` | Stateless compatibility adapter | Reads route plus active state and discards Router item hints | Keep stateless; it may translate legacy route data, but it must not own state or become a second action authority. |
| `services/scales.py` | Existing definition registry | `SCALES`, `ScaleManager`, options, questions, `max_score`, scoring ranges | Make definition access/score validation explicit and reusable. Do not duplicate per-scale legal score sets elsewhere. |
| `core/scale_fsm.py` | Current production value container | `ScaleState`, `delegate_property`, all active/waiting/answer/pause fields | Retire the duplicate container after the Runtime integration is green. Preserve only non-runtime signal helpers if still required, with tests proving they are not administration state. |
| `core/scoring.py` | Natural-language scoring heuristics | `infer_scale_score_from_text`, short-answer/frequency helpers | Keep or move behind a pure answer-interpreter boundary. It must return a candidate/result and never mutate Runtime. |
| `core/tags.py` | Tag parser | `parse_scale_tags` accepts score syntax without scale-specific legality | Keep syntax parsing as metadata; route candidates through the definition validator and current-item check before Runtime acceptance. |
| `conversation/contracts.py` | Phase 2 frozen contracts | `RouterProposal`, `TurnStateSnapshot`, `TurnSignals`, `TurnDecision` | Do not redesign authority. `TurnStateSnapshot` remains a read-only view populated from Runtime; do not add Router item/score fields. |
| `conversation/turn_policy.py` | Single action decider | Uses active/waiting/completed snapshot fields | Keep exactly one policy decision; consume Runtime-derived snapshot values. `ScaleRuntime` must not expose `decide_action()`. |
| `conversation/turn_signals.py` | Stateless observations | Interruption and deterministic candidate signals | Keep pure; no answer writes or Runtime transitions. |
| `services/pipeline.py` | Current transition and answer owner | Delegated `_active_scale*`, `_scale_answers`, `_apply_turn_decision`, tag/inference mutation, advance/pause/resume helpers | Replace direct state mutation with Runtime commands and a thin read facade. Remove `ScaleState` import, delegate properties, and shadow answer writes. |
| `ui/main_window.py` | UI shadow/read state | `_scale_tags`, `_scale_interrupted_by_relaxation`, `_resume_scale_after_relaxation`, direct Pipeline resume/incomplete calls | Read Runtime-derived snapshots through a compatibility facade; keep `_scale_tags` only as derived/reporting metadata. Do not redesign relaxation lifecycle. |
| `services/tools/report_tool.py` | Report scoring consumer | Scores `scale_tags` and creates report payloads | Consume a Runtime-derived result snapshot while preserving report shape and historical readers. |
| `services/report_service.py` | Report/session consumer | Accepts scale results and persistence payloads | Keep API compatibility; do not make it a second state owner. |
| `data/data_manager.py` | Session artifact persistence | Persists session files and delegates progress updates; no active-scale transition API | Keep persistence-only; accept Runtime-derived report data and never feed current scale state. |
| `data/treatment_progress.py` | Cross-session derived history | Stores `scale_scores` and `scale_trend` in completed-session summaries | Keep as historical/derived data; it cannot select or resume a current item. |
| `services/stats_service.py` | Statistics reader | Reads latest/first `scale_scores` for longitudinal summaries | Keep read-only/derived; do not use statistics as current Runtime input. |
| `services/agent_service.py` | Router adapter | Route proposal only after Phase 2 | No code path may reintroduce item, score, urgency, or executable scale control fields. |
| `services/session_orchestrator.py`, `core/session_fsm.py` | Session/relaxation lifecycle | Session state and relaxation transitions | Read scale completion/incomplete information only; SessionEngine authority migration is out of scope. |
| `main.py`, `safety/**`, `knowledge_base/**`, `services/rag_service.py` | Phase 1 runtime boundary | Safety resources are detached; production RAG is ordinary-only | No Phase 3 changes may reconnect `safety/resources` or alter the Phase 1 import boundary. |

## Test and evidence inventory

| Test path | Current coverage | Phase 3 treatment |
|---|---|---|
| `tests/test_assessment_runtime.py` | Two tests for the thin Runtime skeleton | Replace/extend with the full Runtime contract, per-scale legal scores, snapshots, pause/resume, completion, duplicate and out-of-order rejection. |
| `tests/test_core_scale_fsm.py` | Direct `ScaleState` defaults, reset, delegation, and Pipeline aliases | Migrate assertions to Runtime ownership; retain only a removal/boundary assertion for `ScaleState`. |
| `tests/integration/test_pipeline_e2e.py` | Reads `_active_scale`, `_scale_answers`, and legacy retroactive scoring behavior | Assert Runtime snapshots/results and current-turn metadata instead of Pipeline private state. Preserve ordinary scale behavior. |
| `tests/test_pipeline.py` | Tag parsing/cleaning and response behavior | Add tag-as-metadata and illegal-score/non-current-item rejection coverage. |
| `tests/test_core_tags.py` | Syntax extraction for `[SCALE:...]` | Keep parser syntax tests; do not make the parser an authority. |
| `tests/test_core_scoring.py` | Natural-language score heuristics | Use as interpreter fixtures; add ambiguous/refusal/no-mutation tests at the new boundary. |
| `tests/test_turn_authority*.py` | Phase 2 Router/Policy/Decision boundary | Keep unchanged except for source-of-snapshot assertions; prove exactly one `TurnDecision` remains. |
| `tests/test_scale_runtime.py` (planned) | No file yet | New deterministic Runtime state-machine contract tests. |
| `tests/test_scale_definitions.py` (planned) | No file yet | New canonical registry/domain/item-count validation tests. |
| `tests/test_scale_answer_interpreter.py` (planned) | No file yet | New accepted/ambiguous/refusal/pause classification tests with no Runtime calls. |
| `tests/test_scale_state_boundary.py` (planned) | No file yet | Static ownership tests for Pipeline/UI/Router/Runtime and forbidden second authority. |

## Preflight conclusion

The current code does **not** yet have Runtime integration. `ScaleState` is
the value container, while `ConversationPipeline` is the transition owner;
the `ScaleRuntime` skeleton is not on the production path. The migration must
therefore be staged as:

1. Make `SCALES` the validated definition source.
2. Build and test the deterministic Runtime contract in isolation.
3. Build a pure answer-interpreter boundary.
4. Route Phase 2 decisions and all scale mutations through Runtime.
5. Convert Pipeline/UI/report code to Runtime read models and derived metadata.
6. Remove `ScaleState` and legacy delegation only after integration tests prove
   there is no remaining shadow owner.

The Phase 2 baseline is green (`351 passed`). No Phase 3 production code has
been changed, and no Phase 4/5 migration is implied by this inventory.

## Boundary checklist before implementation starts

- [x] Branch is `codex/a100-vllm-safety` at Phase 2 HEAD `078d186...`.
- [x] Full baseline is `351 passed`, `0 failed`, `0 skipped`.
- [x] Router proposal has no executable item or score ownership.
- [x] Current state copies and mutation sites are listed above.
- [x] Canonical scale domains are recorded: PHQ/GAD `0..3`, PCL `0..4`.
- [ ] Runtime implementation has not started.
- [ ] No SessionEngine authority migration has started.
- [ ] No crisis/Guard or deployment change is included.
