# Phase 5 preflight: intervention and end-policy inventory

**Status:** pre-implementation inventory; production code is unchanged.

- Date: 2026-08-14
- Branch: `codex/a100-vllm-safety`
- Baseline HEAD: `2d650b3728020fb50b1ca402502188fd651066d8`
- Phase 4 implementation ancestor:
  `aa1a0416fc53cb0ce605be8da648ce40a0f058e1`
- Python executable used for the local baseline:
  `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe`
- Accepted baseline: `405 passed`, `0 failed`, `0 skipped`

This inventory is a read-only map of the existing policy and lifecycle
signals. It does not authorize edits. “Policy” means deciding whether a user
or system signal becomes a `TurnDecision`; “execution” means carrying out an
already approved decision through SessionEngine, ScaleRuntime, media, or UI.

## Scope and method

The scan covered production Python under `app/`, `assessment/`,
`conversation/`, `core/`, `game/`, `services/`, `ui/`, `deployment/`,
`config.py`, and `main.py`. It excluded `.venv`, `.git`, `__pycache__/`,
generated output, and tests except where a current contract is named. The
search followed both exact names and behaviorally equivalent paths:

```text
MIN_ROUNDS_FOR_RELAXATION, relaxation_used,
recommend_relaxation, legacy_relaxation_candidate,
relaxation_candidate, _pending_relaxation_after_scale,
recommend_game, legacy_game_candidate, game_candidate,
entertainment, AGENT_ENTERTAINMENT_KEYWORDS, 无聊,
explicit_end_requested, exit_intent, END_PATTERNS, end_type,
allow_force_relaxation, forced relaxation, time_warning,
time_limit, continued_after_time_limit, pending end,
post_relaxation, relaxation_tool, END_GOAL_ACHIEVED, END_QUIT
```

## Disposition vocabulary

- **migrate to TurnPolicy:** retain only as an immutable signal/input and
  enforce the rule in the one policy decision.
- **retain as execution:** keep the operation, but it may run only after a
  `TurnDecision`/engine command.
- **derived/reporting only:** keep a fact or display projection that cannot
  influence a transition.
- **remove competing authority:** delete or narrow a duplicate rule/path.
- **compatibility only:** preserve an adapter temporarily; production code
  must not use it as the policy source.
- **Phase 5 follow-up:** intentionally unchanged in the implementation, but
  must not be expanded into a new authority while this phase is active.

## Ownership matrix

| ID | Rule/state and current symbol | Current location and evidence | Current decision owner | Current executor | Phase 5 target owner | Disposition / required boundary |
|---|---|---|---|---|---|---|
| R1 | Proactive relaxation threshold `MIN_ROUNDS_FOR_RELAXATION = 8` | `config.py:581-587`; `services/pipeline.py:1677-1691` adds a system suffix warning; `TurnPolicy` does not currently enforce it for Router proposals | Mixed: prompt text plus Router/`TurnPolicy` proposal | Pipeline/LLM response | `TurnPolicy` using immutable round-count signal | **migrate to TurnPolicy**; the prompt is guidance only and must not be the gate. |
| R2 | Completed/use fact `relaxation_used` / `completed_relaxation` | `conversation/contracts.py:149`; `services/pipeline.py:466`; `services/report_service.py:55,58,163-166`; `core/session_fsm.py:40,123` | Report/FSM copies jointly affect policy | Report service records completion; Engine reads command metadata | One explicit policy fact plus report-derived read-only completion | **remove competing authority**; distinguish completed intervention from proactive-offer count. |
| R3 | Router action `recommend_relaxation` | `conversation/contracts.py:48,97,103-112`; `conversation/turn_policy.py:103-113`; `services/agent_service.py:618-636,694-714` | Router proposes; `TurnPolicy` currently checks only used/confidence | Pipeline `_apply_turn_decision()` at `518-545`; UI highlight at `598-601` | `TurnPolicy` after explicit/proactive eligibility checks | **migrate to TurnPolicy**; keep Router as suggestion only. |
| R4 | Legacy relaxation candidate `legacy_relaxation_candidate` / `_pending_relaxation_after_scale` | `conversation/turn_signals.py:16-32`; `services/pipeline.py:405,987,1260-1267` | Pipeline creates candidate and feeds signals; policy accepts at `turn_policy.py:136-141` | Pipeline stores candidate and emits recommendation metadata | Explicitly typed signal or remove after post-scale policy is defined | **remove competing authority**; no hidden post-scale recommender may bypass policy. |
| R5 | Soft relaxation heuristics and secondary recommender (`soft relaxation heuristic`) | `services/tools/relaxation_tool.py:1-55`; `ui/main_window.py:1737-1751`; `services/report_service.py:546-588` | 3B/72B recommendation helper, outside `TurnPolicy` | UI calls tool for end dialog; report service calls models | `TurnPolicy` selects eligibility; a pure media-type helper may be retained | **compatibility only** initially, then **remove competing authority**; it must never open a recommendation or end gate. |
| R6 | Per-session proactive marker `_relaxation_recommended_this_session` | `services/pipeline.py:403,432-437,1653-1655`; passed to Router as `relaxation_done` | Pipeline set is treated as “done” | Pipeline reset/decision path | Single immutable snapshot fact supplied to `TurnPolicy` | **remove competing authority**; name/meaning must be separated from completed media. |
| R7 | Router action `recommend_game` | `conversation/contracts.py:49,98,115-121`; `conversation/turn_policy.py:115-121`; `services/agent_service.py:618-636,711-714` | Router suggestion and current policy branch; no explicit-request cross-check | Pipeline `_game_candidate` and UI game highlight | `TurnPolicy` only after explicit game-request signal | **migrate to TurnPolicy**; Router cannot authorize game by itself. |
| R8 | `legacy_game_candidate` / `_game_candidate` | `conversation/contracts.py:162`; `conversation/turn_signals.py:17-32`; `services/pipeline.py:407,544,1180-1181` | Pipeline/legacy signal can reach policy | Pipeline result intent/media metadata | Explicit user-request signal; no legacy candidate | **remove competing authority**. |
| R9 | Entertainment intent and boredom keywords | `config.py:816-848` (`AGENT_ENTERTAINMENT_KEYWORDS` includes `无聊`); `services/agent_service.py:188-211,351-359`; Router prompt `agent_service.py:633-635` says “想玩/无聊” -> game | 3B keyword/model classifier | Pipeline sets `result.intent = entertainment` at `1353-1355`; UI/media can react | Signal classifier may report context; only explicit request maps to game proposal | **migrate to TurnPolicy signal**; “无聊/没意思” alone must remain chat. |
| R10 | Explicit user end `explicit_end_requested` | `conversation/turn_signals.py:27`; `core/scoring.py:126-143` with weak-response exclusions | Deterministic detector feeds `TurnPolicy` priority 1 | Pipeline maps `TurnDecision` to `end_type`; Engine executes command | `TurnPolicy` remains sole action authority | **retain as signal / migrate to TurnPolicy**; add direct-end tests and preserve weak-response behavior. |
| R11 | Router/legacy `exit_intent` | `conversation/contracts.py:109-113,254`; `services/agent_service.py:717`; `PolicyDecision.exit_requested` at `conversation/contracts.py:239-276` | Router/compatibility adapter | Pipeline/Coordinator compatibility paths | Explicit signal first; Router exit is only a low-authority proposal | **compatibility only**; must not bypass `TurnPolicy`. |
| R12 | 72B output `END_PATTERNS` and `end_type` tags | `core/tags.py:14-19`; `services/pipeline.py:1153-1161`; `PipelineResult.end_type` at `pipeline.py:331` | Main LLM currently emits metadata; pipeline already ignores a conflicting tag | Pipeline report/TTS suppression and UI event adapter | `TurnDecision` sets end; tags remain cleaning/report metadata only | **retain as derived/reporting only**; static test that raw tags cannot end a turn. |
| R13 | End-type enum and mapping | `core/types.py`; `services/pipeline.py:263-268,547-560`; `app/contracts.py:96-109` | Mixed legacy result and Engine command paths | SessionEngine/report flow | End reason is carried by one approved decision/command | **retain as execution contract**; no new end authority or crisis path. |
| R14 | Forced-relaxation-before-end rule | `core/session_fsm.py:107-139`; `app/engine.py:304-375` (`allow_force_relaxation`, `_NO_FORCE_END_TYPES`); UI readiness flow `main_window.py:1568-1643` | Session FSM/Engine and UI readiness checks duplicate the rule | Engine emits forced recommendation; UI opens dialogs/media | `TurnPolicy` approves/rejects; Engine only executes approved force command | **remove competing authority**; explicit user end must be no-force/direct. |
| R15 | End readiness and incomplete-scale prompt | `ui/main_window.py:1568-1661` (`_request_end_with_readiness_check`, `_get_end_readiness_state`); `get_incomplete_scales()` | MainWindow currently decides whether to prompt | UI dialog and Runtime resume helpers | Explicit end decision from `TurnPolicy`; UI renders only an event/command | **remove competing authority** for explicit end; preserve UI-only dialogs for non-explicit product flows only if policy authorizes them. |
| R16 | Pending end after playback | `app/engine.py:115-118,343-361,412-418`; legacy UI queue paths `main_window.py:1582-1591` | SessionEngine is lifecycle owner after Phase 4; UI still has wrappers | Engine defers command until media callback | SessionEngine execution, no policy change | **retain as execution**; never convert a deferred explicit end into forced relaxation. |
| R17 | End direct/relax dialog paths | `ui/main_window.py:1663-1800` (`_show_end_decision_dialog`, `_end_session_directly`, `_end_session_with_relaxation`) | UI chooses between end/continue/relax after readiness checks | UI starts media or submits EndSessionCommand | UI displays only a decision already authorized; explicit end bypasses this chooser | **remove competing authority** from explicit-end path; retain rendering/command adapters where still needed. |
| R18 | Time warning one-shot marker | `services/report_service.py:55,156,194-231`; `app/engine.py:76,112,489-514` | ReportService and Engine both mutate warning state | Report/UI/Engine events | One SessionEngine writer; report receives a derived fact | **remove competing authority**; preserve current warning threshold and single-shot behavior. |
| R19 | Hard time-limit ask `time_limit_prompt_shown` | `services/report_service.py:56,157,218-223,233-245`; `app/engine.py:77,113,516-531`; Pipeline emits `time_limit_ask` at `1321-1323` | Three potential paths (ReportService, Pipeline, Engine) | UI `_ask_continue_or_end()` at `main_window.py:1288-1306` | One deterministic Engine check/event and one UI dialog | **remove competing authority**; reaching the limit opens a choice, never silently ends. |
| R20 | Continue-after-time-limit `continued_after_time_limit` | `services/report_service.py:57,158,203-207`; UI event mirror `main_window.py:643-647`; Engine `_time_limit_continue_chosen` and `AcknowledgeTimeLimitCommand` | Engine plus report/UI compatibility mirrors | Engine command and report metadata | SessionEngine one-shot marker; report is derived/reporting | **retain as execution fact**, remove any second writer that can trigger policy. |
| R21 | `continue/end` dialog and `acknowledge_time_limit` | `ui/main_window.py:1288-1319`; `app/contracts.py:131-140,263-274`; `app/engine.py:482-487` | UI owns dialog; Engine owns acknowledgement command | UI sends `AcknowledgeTimeLimitCommand` or EndSessionCommand | Engine event/command boundary; no model involvement | **retain as execution** with exactly-once tests. |
| R22 | Post-relaxation lifecycle, `continue_chat`, and pending end after relaxation | `core/session_fsm.py:27,39`; `app/engine.py:394-429` (`ContinueChatCommand`, `_pending_end` resumed after `RelaxationFinishedCommand`); UI `main_window.py:1112-1155,1229-1286,1425-1438` | Engine owns state; UI has timers/greetings and auto-end queue. The older conceptual names `post_relaxation`, `continue_chat`, and `pending_end_after_relaxation` are represented by these event/command paths rather than a second state object. | Engine event, UI media/TTS | Engine executes; `TurnPolicy` handles a later user turn | **retain as execution**; remove positive-feedback auto-end semantics and keep any pending explicit end as an engine command. |
| R23 | UI/pipeline post-relaxation and interruption flags (`relaxation_active`, `relaxation_completed`, `_scale_interrupted_by_relaxation`, `_resume_scale_after_relaxation`) | Phase 4 removed the named UI shadow fields; remaining behavior is in `ui/main_window.py:84-87,1112-1147,1593-1610,1709-1726` and `services/pipeline.py:402,405,1260-1267` | MainWindow/Pipeline legacy shadows or compatibility names; `ScaleRuntime` owns actual pause/resume | UI timer and Pipeline candidate | `ScaleRuntime` snapshot + Engine event; policy signal on next turn | **remove competing authority**; explicitly prove the legacy names are absent or derived and never restore a stale item/auto-end flag. |
| R24 | Scale pause/resume when intervention interrupts | `assessment/scale_runtime.py` (mutable owner); UI direct helpers `main_window.py:1593-1610,1709-1726`; Pipeline accepts/pauses at `pipeline.py:1200-1242` | Runtime owns questionnaire; UI currently decides when to resume before end | Runtime command and its actual snapshot | ScaleRuntime remains owner; policy only approves interruption | **retain as execution**; add tests that resume uses actual `current_item`. |
| R25 | Pipeline recommendation/session flags | `services/pipeline.py:402-408,432-437,540-545,1176-1181` | Pipeline holds policy candidates and session markers | Pipeline result/UI highlight | Immutable per-turn metadata only; policy snapshot/Engine fact | **remove competing authority**; no copied policy state. |
| R26 | ReportService relaxation recommendation helper | `services/report_service.py:546-588`; called by `services/tools/relaxation_tool.py:45-50` and UI `main_window.py:1743-1748` | 3B/72B helper outside TurnPolicy | UI end dialog chooses a tag | Pure type-selection helper only, after policy approval | **compatibility only** then **remove competing authority**; it cannot approve media/end. |
| R27 | `PolicyDecision`, `DialogueAction`, `ScaleAction` compatibility contracts | `conversation/contracts.py:216-279`; coordinator now records a sanitized decision | Legacy adapters/tests | Coordinator compatibility journal | `RouterProposal`/`TurnDecision` only in production | **compatibility only**; do not add Phase 5 rules to this model. |
| R28 | SessionEngine forced-relaxation and timeout handlers | `app/engine.py:304-375,482-535`; `core/session_fsm.py:107-139` | Engine/FSM currently decides force eligibility | Engine transition/event writer | Engine executes commands; eligibility arrives from policy | **remove competing authority** while retaining single-writer lifecycle. |
| R29 | 72B prompt rules for end/relax/game | `config.py:484-499` (END tags), `AGENT_*` prompts in `config.py:780-848`; `services/agent_service.py:614-652` | Prompt/model currently suggests actions and tags | Pipeline parses/cleans output | Prompt is language guidance; only policy signals/decision are authoritative | **derived/reporting only / compatibility**; update only in implementation with prompt-boundary tests. |
| R30 | MainWindow local minimum-round import and UI gates | `ui/main_window.py:29-30` imports `MIN_ROUNDS_FOR_RELAXATION`; readiness and highlight branches at `569-606,1568-1800` | UI can visually/behaviorally gate actions | Qt callbacks and dialogs | UI renders final decision; no local eligibility check | **remove competing authority**; keep UI-only presentation. |

## Direct mutation and decision map

The following writes are the high-risk paths for Phase 5 boundary tests:

```text
conversation/turn_policy.py
  RouterAction.RECOMMEND_RELAXATION / RECOMMEND_GAME branches
  legacy_relaxation_candidate / legacy_game_candidate fallbacks
  explicit-end priority and Router END_SESSION fallback

services/pipeline.py
  _relaxation_recommended_this_session
  _pending_relaxation_after_scale
  _relaxation_candidate / _game_candidate
  report.is_over_limit() -> time_limit_ask emission
  raw END/REC tag parsing and result.end_type/relaxation_rec

services/agent_service.py and config.py
  entertainment/无聊 keyword fallback
  Router prompt rule “想玩/无聊 -> recommend_game”
  END/REC prompt examples and legacy normalized booleans

ui/main_window.py
  _request_end_with_readiness_check()
  _get_end_readiness_state(), _show_end_decision_dialog()
  _end_session_directly(), _end_session_with_relaxation()
  timeout dialog and report-service marker mirrors
  post-relaxation timers and auto_end_session queue message

core/session_fsm.py / app/engine.py
  evaluate_session_end() forced-relaxation branch
  allow_force_relaxation and _NO_FORCE_END_TYPES
  timeout one-shot markers and CheckTimeLimitCommand handling

services/report_service.py / services/tools/relaxation_tool.py
  should_warn_time_limit(), is_over_limit()
  recommend_relaxation_strategy(), RelaxationRecommendationTool.execute()
```

## Required Phase 5 test matrix

The implementation must add or update tests for each row below before the
final regression:

| Area | Required assertions |
|---|---|
| Explicit end | Each current explicit phrase yields one `END_SESSION`; weak “嗯/好吧/行” does not; unfinished scale and no completed relaxation do not insert a gate. |
| Positive feedback | “好多了/轻松了/舒服点了” never yields `END_SESSION` without a separate explicit end signal. |
| Relaxation | User request before round 8 can be approved; proactive candidate before round 8 is rejected; proactive candidate at/after round 8 is approved once; a second proactive candidate is rejected; waiting scale answer blocks proactive recommendation. |
| Game | Explicit “想玩游戏” can yield `RECOMMEND_GAME`; “无聊/没意思” alone yields chat/context; Router/model game output without the explicit signal is rejected. |
| Timeout | Warning/ask is emitted once; continue suppresses later asks; reaching the limit does not auto-end; end choice sends one end command. |
| Post-relaxation | Completed media produces the existing event; paused Runtime resumes its actual item; failed media is not recorded as completed; positive feedback does not auto-end. |
| Authority | 72B END/REC tags cannot replace `TurnDecision`; UI readiness flags cannot approve an end; no Engine/Runtime second policy method appears. |
| Compatibility | `PolicyDecision` remains adapter-only or is removed with all callers updated; A100/vLLM/STT/TTS and crisis-detachment boundaries remain unchanged. |

## Static search gate

Before the implementation commit, rerun a production-only search and prove:

1. `TurnPolicy` is the only module returning `TurnDecision` for relaxation,
   game, and end actions.
2. No production call to `relaxation_tool.execute()` or
   `ReportService.recommend_relaxation_strategy()` can approve an action.
3. No `MainWindow` end-readiness branch is reachable for an explicit
   `user_explicit` decision.
4. No raw `END_PATTERNS` or `REC_TAGS` detection mutates lifecycle state.
5. No `RouterProposal` contains item/score control and no Router result is
   treated as authoritative.
6. No Phase 6 contract or session-authority migration appears in the diff.

## Inventory acceptance gate

This inventory is complete for the Phase 5 docs freeze when:

1. Every rule named in the formal specification has a row above with current
   decision owner, executor, target owner, and disposition.
2. All user-listed families are covered: relaxation threshold/use/candidates,
   game/entertainment/boredom, explicit end/exit/tags/end type/forced end,
   timeout/warning/continue dialog, post-relaxation, and Runtime resume.
3. The matrix preserves Phase 2 turn authority, Phase 3 ScaleRuntime
   authority, and Phase 4 SessionEngine single-writer authority.
4. No production code or test file is changed by freezing this inventory; the
   accepted `405 passed` baseline remains the implementation starting point.
5. The next production commit is exactly
   `refactor: unify relaxation game end and timeout routing`, and Phase 6 is
   explicitly out of scope.
