# VoiceChat3 Phase 3 formal implementation specification

## Migrate all scale state to `ScaleRuntime`

- Status: specification and preflight only; implementation is not started.
- Date: 2026-08-14
- Branch baseline: `codex/a100-vllm-safety`
- Baseline HEAD: `078d186ff42360e4a1f04d3b84315fd852f46f66`
- Phase 2 implementation: `3fa975b97217e4a7ce3f23f6d2ffabf211885349`
- Baseline: `351 passed in 65.32s`, `0 failed`, `0 skipped`
- Baseline executable: `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe`

This is the formal Phase 3 execution contract. It intentionally stops before
production edits so the state inventory and baseline can be reviewed first.

## 1. Objective

Phase 3 removes the split ownership of questionnaire state. The desired
runtime path is:

```text
RouterProposal
      ↓
TurnPolicy.decide()                 (the only action decision)
      ↓
exactly one TurnDecision
      ↓
Pipeline execution adapter
      ↓
ScaleRuntime                         (the only scale-administration state)
      ├── current item / waiting state
      ├── answer acceptance and progression
      ├── pause / resume
      └── completion / report snapshot
```

The migration is about **ownership and deterministic state transitions**, not
about adding another intelligent agent. `ScaleRuntime` executes a decision it
receives; it never decides whether the turn is chat, scale, relaxation, game,
or end-session.

## 2. Frozen boundaries from Phase 2

The following contracts are already accepted and must remain intact:

1. `RouterProposal` is a non-executable suggestion.
2. `TurnStateSnapshot` and `TurnSignals` are read-only observations.
3. `TurnPolicy` is called exactly once for a turn.
4. `TurnDecision` is the only executable per-turn action contract.
5. Router JSON contains no executable item or score control. In particular,
   do not add `requested_item`, `item`, `target_item`, `scale_score`,
   `accepted_score`, or equivalent Router fields.
6. The `|||` response protocol, existing scale tag syntax, report shape, and
   normal TTS/UI flow remain compatible.

The current optional answer-looking fields on the frozen `TurnDecision` model
are compatibility fields. Phase 3 must not populate them from Router output or
create a second decision path. Answer interpretation is a separate result
that is validated by `ScaleRuntime` after the authoritative action decision.

## 3. Non-goals and forbidden changes

Phase 3 must not:

- redesign `RouterProposal`, `TurnPolicy`, or the exactly-one-decision rule;
- add `ScaleRuntime.decide_action()`, `ScaleRuntime.route()`, or any second
  business decision authority;
- let Router, 72B dialogue output, UI, or report code write active item,
  waiting, answers, pause, or completion state directly;
- migrate `SessionEngine`, `SessionFSM`, or lifecycle authority;
- redesign relaxation, game, post-relaxation, or session-end lifecycle;
- reconnect crisis/Guard behavior or import `safety/resources` into production;
- alter A100/vLLM models, ports `127.0.0.1:8000`/`127.0.0.1:8001`, GPU
  allocation, STT, TTS, or desktop launch architecture;
- introduce Phase 4/5 names or contracts such as authoritative session
  migration or a new `ScaleRuntime` action policy.

## 4. Target ownership model

### 4.1 Mutable owner

`assessment.ScaleRuntime` is the only mutable owner of scale-administration
state. It must own at least:

```text
active_scale: str | None
current_item: int | None
waiting_for_answer: bool
answers_by_scale: dict[str, dict[int, int]]
completed_scales: set[str]
paused: bool
resume_item: int | None
```

The implementation may retain `administered_scales`, an internal queue,
refusal/defer/ask compatibility counters, and pause counters inside the
Runtime when they are required by existing behavior. If retained, they must
not also exist as live Pipeline or UI copies. Symptom signal counters are
explicitly observation data for `TurnSignals`; they are not questionnaire
answers and do not authorize a second state owner.

### 4.2 Immutable read model

Add a frozen `ScaleRuntimeSnapshot` (name may be adjusted only if an equally
clear name is approved) containing defensive copies/immutable views of the
Runtime fields. A snapshot must be safe to hand to:

- `TurnStateSnapshot` construction;
- prompt/context builders;
- UI read-only rendering;
- report/result adapters;
- tests and event journal records.

Callers must not receive live mutable dictionaries or sets. A report may
convert tuples/mappings back to its established JSON shape, but it cannot write
back to the Runtime.

### 4.3 Decision and state responsibilities

| Concern | Owner | Prohibited owner |
|---|---|---|
| Whether this turn starts/continues/pauses a scale | `TurnPolicy` producing `TurnDecision` | Router, Runtime, 72B, UI |
| Which scale is requested | `TurnPolicy` after validating `RouterProposal`/signals | Router alone |
| Which item is current/next | `ScaleRuntime` and canonical definitions | Router, 72B, UI |
| Whether natural language maps to a score | Pure answer interpreter | Runtime, Router, report |
| Whether an accepted score mutates state | `ScaleRuntime.accept_answer()` | Tag parser, Pipeline dict write |
| Pause/resume item preservation | `ScaleRuntime` | UI shadow dictionary |
| Scale totals/severity | canonical definitions + report adapter | duplicated constants |
| Session/relaxation lifecycle | existing Session/UI compatibility path | Phase 3 Runtime |

## 5. Runtime contract

### 5.1 Required commands

The concrete return type may be a frozen result value, but the following
semantics are mandatory:

```text
reset() -> ScaleRuntimeSnapshot
start(scale_name: str) -> RuntimeUpdate
accept_answer(item: int, score: int) -> RuntimeUpdate
request_clarification() -> RuntimeUpdate
pause() -> RuntimeUpdate
resume() -> RuntimeUpdate
snapshot() -> ScaleRuntimeSnapshot
get_incomplete_scales() -> read-only result
get_results() -> read-only report result
```

`RuntimeUpdate` must expose enough information for the Pipeline to build the
next prompt and `PipelineResult`, without exposing live mutable state. The
method names may be adapted to the existing code style, but the behavior and
ownership boundary may not be weakened.

### 5.2 Runtime invariants

1. `start()` accepts only a registered scale from the canonical definitions.
2. There is at most one active scale. Starting a different scale while one is
   active is rejected; Router cannot switch it.
3. Starting a previously touched but incomplete scale preserves its existing
   answers and selects the first unanswered item. Starting a completed scale
   is rejected unless the session has been explicitly reset.
4. Starting a scale selects the first unanswered item; Router item hints are
   ignored.
5. A score is accepted only for the active scale's current item, while the
   Runtime is waiting for that answer, and only if the score is legal for that
   scale.
6. An accepted answer is recorded exactly once. Duplicate or out-of-order
   answers are rejected without overwriting an existing answer.
7. After acceptance, the Runtime selects the next unanswered item from the
   definition. It never increments beyond the definition's item count.
8. When all items are answered, the scale is added to `completed_scales`, the
   active scale is cleared, and waiting/pause state is cleared.
9. `request_clarification()` never writes an answer and keeps the same item and
   `waiting_for_answer=True`.
10. A refusal/interruption is not converted to a score. The authoritative
   `TurnDecision.PAUSE_SCALE` calls `pause()` and preserves the actual
   unanswered item.
11. `resume()` chooses the first unanswered item from `answers_by_scale`, not a
    stale UI item. It clears pause state and exposes the item for the next
    question.
12. `reset()` clears all scale-administration state and restores a fresh
    snapshot. It does not reset unrelated session or relaxation state.
13. Every public mutating method is deterministic and does not call an LLM,
    network service, STT/TTS service, Router, or UI callback.

### 5.3 Waiting and question presentation

The Runtime state must make the question lifecycle explicit. A start or
resume command establishes the current item; the execution adapter marks the
item as awaiting an answer when the system question is emitted. Accepting an
answer clears the waiting flag until the next item is presented. If the
existing pipeline needs a separate `present_current_item()` command to retain
streaming behavior, that command is allowed as a deterministic state command;
it must not choose a new business action.

## 6. Canonical scale definitions

`services/scales.py:SCALES` remains the single source of truth. Phase 3 may
add typed accessors or a small immutable adapter, but it must not create a
second table of question counts or score domains.

The definition contract is:

| Scale | Items | Legal item scores | Maximum |
|---|---:|---|---:|
| PHQ-9 | 9 | `0, 1, 2, 3` | 27 |
| GAD-7 | 7 | `0, 1, 2, 3` | 21 |
| PCL-5 | 8 | `0, 1, 2, 3, 4` | 32 |

The legal score set must be derived from each definition's `options`. The
definition adapter must provide, without duplicated literals:

- normalized scale name;
- question text and item count;
- legal score values and labels;
- maximum score;
- total-score calculation and severity ranges;
- validation for a partial or completed answer map.

`ScaleManager.score_scale()` must reject or explicitly report invalid scores
instead of silently summing them. Existing partial-report behavior must remain
available, but invalid data must not be represented as an accepted Runtime
answer.

## 7. Answer interpretation boundary

Natural language understanding is separate from state mutation.

### 7.1 Interpreter result

Create a pure `ScaleAnswerInterpreter` (or an equivalently named module) that
returns a frozen value such as:

```text
status: accepted | ambiguous | refusal | pause | unmatched
score: int | None
scale_name: str | None
item: int | None
reason: str
```

The interpreter receives text, the active scale, current item, and the
canonical definition. It may reuse the existing `core.scoring` phrase
heuristics, but it must not import or mutate `ScaleRuntime`.

Clear existing examples remain valid where the definition allows them:

- PHQ/GAD: `没有` → 0, `好几天` → 1, `一半以上的时间` → 2,
  `几乎每天` → 3;
- PCL-5: existing option phrases map to 0–4.

The implementation must not claim that every free-form phrase is reliably
scorable. Phrases such as `有时候吧`, `还行`, or `可能挺多的` are ambiguous
unless the interpreter has a definition-backed, unambiguous mapping.

### 7.2 Required behavior

- `accepted` is the only status that may call `ScaleRuntime.accept_answer()`.
- `ambiguous` calls `request_clarification()` and leaves item/answers intact.
- `refusal` or `pause` becomes an observation for the already-authoritative
  `TurnPolicy`; it never writes a score. The resulting `PAUSE_SCALE` calls
  `ScaleRuntime.pause()`.
- `unmatched` leaves the current item waiting and uses the existing ordinary
  clarification response path.
- A parsed `[SCALE:...]` tag is only an answer candidate/metadata. It must be
  validated against the current Runtime scale/item and canonical legal scores
  before it can be accepted. A tag cannot change the TurnDecision.

## 8. Per-turn integration sequence

The migrated pipeline must follow this sequence without a second action judge:

1. Obtain the user text/transcript.
2. Build the Router proposal and read-only observations.
3. Call `TurnPolicy.decide()` exactly once.
4. Apply the decision to the Runtime only for scale actions:
   `START_SCALE`, `CONTINUE_SCALE`, or `PAUSE_SCALE`.
5. Build the prompt from a Runtime snapshot and canonical question definition.
6. Let the 72B model express the already-authorized question naturally. It
   cannot select an item, score an answer, or change the action.
7. Parse response tags as metadata only.
8. Interpret the participant's answer through the pure interpreter.
9. If and only if interpretation is `accepted`, call
   `ScaleRuntime.accept_answer(current_item, score)`.
10. For ambiguity/refusal/unmatched input, keep the Runtime item unchanged and
    produce an ordinary clarification/pause response.
11. Build `PipelineResult`, `TurnStateSnapshot`, and report data from Runtime
    snapshots. No Pipeline/UI dictionary write may become the source of truth.

The exact existing streaming/TTS order may remain when it does not violate
these ownership rules. The migration must not reintroduce a post-LLM business
decision based on a tag.

## 9. Compatibility adapters

To avoid a simultaneous UI/report rewrite, the first integration may retain
thin read/command facades on `ConversationPipeline`:

- `get_active_scale_state()`
- `get_active_scale_question_text()`
- `restore_active_scale()`
- `force_resume_incomplete_scale()`
- `get_incomplete_scales()`
- `get_scale_results()`

These methods must delegate to Runtime snapshots/commands. They may not
recreate state, maintain a second answer map, or calculate completion from a
different source. They can be removed in a later cleanup after all UI/report
consumers are migrated.

`PipelineResult.scale_tags` remains a current-turn metadata field for
compatibility. It is not a state store. `MainWindow._scale_tags` may remain as
a derived report cache during the transition, but every value must originate
from a Runtime-derived result and it must not drive the next item or waiting
state.

## 10. Exact file plan

### Production files

- `assessment/scale_runtime.py`: implement the deterministic Runtime, frozen
  snapshot/update values, legal-score checks, pause/resume, completion, reset,
  and report read models.
- `assessment/scale_policy.py`: keep stateless compatibility translation;
  remove any dependency on mutable Pipeline aliases if introduced by the
  migration.
- `assessment/__init__.py`: retain stable exports and expose only approved
  Runtime value types.
- `services/scales.py`: add definition accessors/validation derived from
  `SCALES`; preserve existing questions, options, totals, and severity ranges.
- `core/scoring.py`: keep pure candidate heuristics or delegate them to the
  answer-interpreter boundary; no Runtime mutation.
- `core/tags.py`: keep syntax parsing and ensure legality is validated later.
- `services/pipeline.py`: construct one Runtime per session, route all scale
  actions and accepted answers through it, build Phase 2 snapshots from it,
  and remove `ScaleState`/delegate-property ownership.
- `ui/main_window.py`: consume Runtime-derived read models for active,
  incomplete, resume, and report behavior; retain relaxation lifecycle as-is.
- `services/tools/report_tool.py` and `services/report_service.py`: consume
  Runtime-derived results without becoming state owners.
- `conversation/contracts.py`, `conversation/turn_policy.py`, and
  `conversation/turn_signals.py`: preserve Phase 2 authority; only adjust the
  source of snapshot values if required.

### Test files

- Extend `tests/test_assessment_runtime.py` or add
  `tests/test_scale_runtime.py` for the Runtime contract.
- Add `tests/test_scale_definitions.py` for domains, item counts, maximums,
  and invalid-score behavior.
- Add `tests/test_scale_answer_interpreter.py` for accepted, ambiguous,
  refusal, pause, and unmatched results.
- Add `tests/test_scale_state_boundary.py` for static ownership and forbidden
  second-authority checks.
- Update `tests/test_core_scale_fsm.py` to prove the old container is no
  longer production-owned.
- Update `tests/integration/test_pipeline_e2e.py`, `tests/test_pipeline.py`,
  and report/UI tests to use Runtime snapshots and derived metadata.
- Keep all Phase 1 safety-boundary tests and Phase 2 authority tests green.

### Documentation files

- `docs/refactor/phase3_scale_state_inventory.md` records the preflight
  evidence and state copies.
- `docs/refactor/phase3_scale_runtime_spec.md` is this locked execution
  contract.
- A final implementation record may be added only after production changes,
  tests, Git staging, and push are actually complete.

## 11. TDD execution tasks

Each task must be red-first, then green, with no unrelated cleanup.

### Task 0 — freeze and baseline

Files: documentation only.
Actions:

1. Confirm branch `codex/a100-vllm-safety` and baseline HEAD `078d186...`.
2. Run:

   ```powershell
   & 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests -q
   ```

3. Record the actual result. The preflight result is `351 passed`,
   `0 failed`, `0 skipped`.
4. Run `git status --short` and confirm no production changes are present.

### Task 1 — canonical definition adapter

Files: `services/scales.py`, `tests/test_scale_definitions.py` (new), and
focused existing scale tests.

Red tests must prove:

- PHQ-9/GAD-7 reject score 4 and PCL-5 accepts score 4;
- item counts and max scores are 9/27, 7/21, and 8/32;
- legal scores are derived from `SCALES.options`;
- invalid partial data is reported rather than silently summed.

Green implementation may add typed accessors, but no duplicate definition
table is permitted. Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_scale_definitions.py tests/test_core_scoring.py -q
```

### Task 2 — isolated Runtime state machine

Files: `assessment/scale_runtime.py`, `assessment/__init__.py`,
`tests/test_scale_runtime.py` (new), and focused assessment tests.

Red tests must cover start, first unanswered item, accepted score, duplicate
and out-of-order rejection, next-item selection, completion, reset, immutable
snapshot copies, pause, resume after a partially answered scale, and
clarification with no answer mutation.

Green implementation must have no model/policy/UI imports and no
`decide_action()` method. Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_scale_runtime.py tests/test_assessment_runtime.py -q
```

### Task 3 — pure answer interpreter

Files: `assessment/answer_interpreter.py` (new, if selected),
`core/scoring.py`, `tests/test_scale_answer_interpreter.py` (new), and
focused scoring tests.

Red tests must prove clear phrases produce accepted candidates, ambiguous
phrases produce clarification, refusal/pause produces no score, and no test
case can mutate a Runtime through the interpreter. Green code must keep
definition validation in the Runtime, not the interpreter alone.

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_scale_answer_interpreter.py tests/test_core_scoring.py -q
```

### Task 4 — Pipeline decision-to-Runtime integration

Files: `services/pipeline.py`, `conversation/contracts.py` only if an
additive read-model adapter is unavoidable, and pipeline/authority tests.

Red tests must prove:

- one `TurnDecision` is still produced;
- START/CONTINUE/PAUSE call Runtime commands;
- active/current/waiting/completed snapshot values come from Runtime;
- Router item/score hints are ignored;
- Pipeline no longer writes a second answers map or active-item property.

Run the focused authority and integration suites after each green step:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_turn_authority*.py tests/test_scale_runtime.py tests/integration/test_pipeline_e2e.py -q
```

### Task 5 — tag and answer acceptance boundary

Files: `core/tags.py`, `services/pipeline.py`, `tests/test_core_tags.py`,
`tests/test_pipeline.py`, and answer-boundary tests.

Red tests must prove tags remain parseable metadata but cannot switch the
scale, select a different item, accept an illegal score, or overwrite a prior
answer. Ambiguous participant text must retain the current item and waiting
state. Green code passes only validated current-item scores to Runtime.

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_core_tags.py tests/test_pipeline.py tests/test_scale_answer_interpreter.py -q
```

### Task 6 — UI and report read-model migration

Files: `ui/main_window.py`, `services/tools/report_tool.py`,
`services/report_service.py`, UI/report tests.

Red tests must fail if UI resume fields or `_scale_tags` can become the next
item/answer authority. Green code keeps the existing relaxation lifecycle,
but obtains active/incomplete/resume/results data from Pipeline facades that
delegate to Runtime snapshots. `_scale_tags` is explicitly derived metadata.

```powershell
$env:QT_QPA_PLATFORM='offscreen'
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/integration/test_ui_boot_headless.py tests/test_report_service.py tests/integration/test_pipeline_e2e.py -q
```

### Task 7 — remove the legacy owner and enforce boundaries

Files: `core/scale_fsm.py`, `services/pipeline.py`, updated scale tests, and
`tests/test_scale_state_boundary.py`.

Red tests must identify any production import/use of `ScaleState`,
`delegate_property`, Pipeline active/answer shadow fields, UI business writes,
or `ScaleRuntime.decide_action`. Green code removes the duplicate owner only
after all compatibility facades delegate to Runtime.

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_scale_state_boundary.py tests/test_core_scale_fsm.py tests/test_turn_authority_boundary.py -q
```

### Task 8 — final Phase 3 verification and delivery

No Phase 4 work may be included. Before any commit:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests -q
git diff --check
git status --short
git diff --stat
git diff
```

The full suite must pass with `0 failed` and `0 skipped`. The exact changed
file list must be reviewed and staged explicitly; `git add .`/`git add -A`
is forbidden. The implementation commit, when authorized, is:

```text
refactor: migrate all scale state to scale runtime
```

Push only `codex/a100-vllm-safety`, then verify local/remote HEAD and a clean
working tree. Record live local smoke honestly: A100/vLLM, FunASR, and VoxCPM2
are `NOT RUN / environment unavailable` unless a real service/device was
actually exercised.

## 12. Verification gates

Phase 3 is complete only when all gates below are evidenced:

- Runtime unit and integration tests are green;
- legal score domains come from `SCALES`, with no duplicate table;
- ambiguous/refusal answers never mutate Runtime answers;
- pause/resume returns the actual unanswered item;
- Pipeline/MainWindow/ScalePolicy/Router/72B have no independent scale-state
  owner;
- Phase 1 safety boundary and Phase 2 authority suites remain green;
- `python -m pytest tests -q` passes with no skipped tests;
- `git diff --check` is clean;
- exact Phase 3 files are staged and reviewed;
- commit and push verification is recorded;
- no Phase 4/5 contract or production code appears in the diff.

## 13. Explicit Phase 3 stop condition

After the Phase 3 implementation commit and its final verification record,
stop. Do not begin SessionEngine authority migration, Router/TurnDecision
redesign, relaxation lifecycle work, or any other Phase 4/5 task in the same
change set.
