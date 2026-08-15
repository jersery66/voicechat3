# Phase 8 scenario and authority inventory

> Companion inventory for `phase8_e2e_acceptance_spec.md`. This is a read-only
> baseline of the Phase 7 code and tests. It records what already exists,
> what remains only at unit/boundary level, and the exact scenario evidence
> required before Phase 8 can be accepted.

## 1. Inventory status and method

- Status: **complete for the Phase 8 design freeze; implementation is not
  started**.
- Date: 2026-08-15.
- Branch: `codex/a100-vllm-safety`.
- Baseline HEAD:
  `66fdb7d25964f5b49d142643eb8178eca1e4af71`.
- Phase 7 implementation baseline:
  `4858cd90debdd1d68df200f1c5394a288df26098`.
- Baseline command:
  `$env:PYTHONPATH='E:\Anaconda\envs\voice_chat\Lib\site-packages'; E:\Anaconda\python.exe -m pytest tests -q`.
- Baseline result: **471 passed, 1 skipped, 0 failed**. The skip is the
  optional PySide6 offscreen UI module in the current environment. Real A100,
  vLLM, STT, VAD microphone, and TTS hardware smoke is **NOT RUN / environment
  unavailable**.
- Inventory sources: current production modules, all `tests/` files, existing
  integration fakes, and the Phase 1–7 refactor records. No production or test
  files are changed by this inventory.

## 2. Current live path and proposed Phase 8 harness

| Boundary | Current production owner/path | Existing evidence | Phase 8 test seam |
|---|---|---|---|
| Input | `ui/main_window.py`, `services/pipeline.py` | Text-mode pipeline tests and STT/VAD protocol tests | Scripted text plus a FakeSTT transcript; no microphone dependency |
| Router observation | `services/agent_service.py` -> `conversation.contracts.RouterProposal` | `tests/test_turn_authority.py`, integration FakeAgent | Record proposal, timeout, invalid response, and calls |
| Per-turn action | `conversation/turn_policy.py` -> `TurnDecision` | `tests/test_turn_authority*.py`, `tests/test_phase5_policy*.py` | Spy policy records exactly one call and result per accepted turn |
| Scale state | `assessment/scale_runtime.py`, `assessment/answer_interpreter.py` | `tests/test_scale_runtime.py`, `test_scale_answer_interpreter.py`, scale boundaries | Use real runtime/interpreter and assert snapshots before/after each answer |
| Session lifecycle | `app/engine.py` -> `SessionEngine` | `tests/test_app_engine.py`, `test_phase4_lifecycle_boundary.py` | Record commands/events and drive engine with explicit barriers |
| RAG gate | `services/pipeline.py` -> `services/rag_service.py` | `tests/test_phase6_rag_boundary.py`, `test_rag.py` | Fake RAG records calls; assert iff `TurnDecision.needs_rag` |
| Language provider | `services/llm_service.py`, `services/llm_factory.py` | `tests/test_llm_service.py`, `test_llm_factory.py`, pipeline integration | Script chunked provider streams; preserve `commit_history=False` delivery path |
| Response normalization | `conversation/response_builder.py` and pipeline | `tests/test_phase6_prompt_protocol_boundary.py`, pipeline tests | Assert plain text and legacy tags cannot alter actions |
| Generation delivery | `conversation/delivery.py`, `ui/main_window.py` | `tests/test_phase7_delivery_boundary.py` | Use real controller/segmenter/ledger/queue with fake TTS and UI recorder |
| UI delivery | `ui/chat_panel.py`, MainWindow queue | `tests/test_phase7_delivery_boundary.py`, optional headless UI test | Use a generation-aware fake UI unless PySide6 is available |
| Audio | `services/tts_service_voxcpm.py`, CosyVoice adapter | `tests/test_tts_preflight.py`, voice protocols | Fake provider records ordered sentence calls and stop hook |
| History/storage | `conversation.delivery.DeliveryLedger`, `services/llm_service.py`, `data/data_manager.py` | `tests/test_data_manager.py`, Phase 7 ledger tests | Fake history/DataManager assert delivered-only exactly-once writes |
| Report/farewell | `services/report_service.py`, MainWindow report flow | `tests/test_report_service.py`, `test_report_vllm_fallback.py` | Fake report/PDF sink records report-before-farewell order |
| Media/game | `app/engine.py`, UI game/video handlers | `tests/test_app_engine.py`, `test_game_service.py` | Fake media records commands and failed/completed outcomes |
| Session reset | engine, runtime, report, delivery controller | Individual reset tests exist; no cross-domain scenario | Run two sessions in one fixture and assert all reset domains |

The test harness must call real authority components and fake only external
model/audio/device/storage seams. It must not add a production `ScenarioEngine`
or make a recorder authoritative.

## 3. Existing coverage versus missing integrated proof

| Area | Existing tests at baseline | What they prove today | Missing Phase 8 evidence |
|---|---|---|---|
| Ordinary pipeline turns | `tests/integration/test_pipeline_e2e.py`, `tests/test_pipeline.py` | Pipeline executes with fakes and returns a response | Full chain trace across policy, runtime, engine, delivery, and history in one scenario |
| Router/TurnPolicy | `tests/test_turn_authority.py`, `test_turn_authority_pipeline.py` | Proposal/decision ownership and one policy call | Multi-turn scenarios prove no later layer overrides the decision |
| Scale runtime | `tests/test_scale_runtime.py`, `test_scale_state_boundary.py`, `test_scale_answer_interpreter.py` | Deterministic state and answer mapping | Full PHQ/GAD/PCL flows, ambiguity, pause/resume, report handoff |
| Session engine | `tests/test_app_engine.py`, `test_phase4_lifecycle_boundary.py` | Commands/events and reset/deferred end | Lifecycle events combined with real pipeline decisions and delivery cancellation |
| Phase 5 policy | `tests/test_phase5_policy.py`, `test_phase5_policy_boundary.py` | Isolated intervention/end/timeout rules | User-visible multi-turn relaxation/game/end/timeout scenarios |
| Phase 6 RAG/prompt | `tests/test_phase6_rag_boundary.py`, `test_phase6_prompt_protocol_boundary.py` | Gate and prompt/tag boundaries | Four contrasting RAG scenarios inside a full turn trace |
| Phase 7 delivery | `tests/test_phase7_delivery_boundary.py` | Generation, sentence, stale, queue, and ledger units | Delivery integrated with a new turn, policy, engine, history, and report |
| TTS/UI | `tests/test_tts_preflight.py`, voice protocols, optional headless UI | Adapter contracts and isolated UI boot | Ordered participant-visible sentence path and stale UI/audio callbacks |
| History/report | `tests/test_data_manager.py`, report tests | Storage/report behavior in isolation | Delivered-only history and report-before-farewell in a full end flow |
| Session isolation | individual start/reset tests | Local reset behavior | Cross-session reset for scale, relaxation, timeout, generation, and report IDs |
| Failure paths | agent timeout, storage, report, TTS preflight tests | Individual errors do not crash adapters | One complete trace per Router/RAG/72B/TTS/media/report failure |

## 4. Scenario-to-evidence matrix

Each row is a required Phase 8 scenario ID. `Existing evidence` is not a
substitute for the Phase 8 test; it identifies reusable fixtures and assertions.

| ID | Scenario | Current source/fixture to reuse | Required new trace evidence | Disposition |
|---|---|---|---|---|
| A1 | Chat, `needs_rag=False` | `test_pipeline_e2e.py::TestNormalChat`, `FakeLLM` | Proposal -> one decision -> zero RAG -> visible/delivered history | ADD E2E |
| A2 | Chat, `needs_rag=True` | `test_phase6_rag_boundary.py`, `FakeRAG` | Exactly one curated query and no second RAG decision | ADD E2E |
| A3 | Psychology keywords but RAG false | Phase 6 gate tests | No keyword override and ordinary delivery | ADD E2E |
| A4 | Simple wording but RAG true | Phase 6 gate tests | Decision alone enables retrieval | ADD E2E |
| B1 | Complete PHQ-9 | `test_scale_runtime.py`, `FakeAgent`, `ScaleAnswerInterpreter` | All items, 0–3 validation, runtime-only item/score, report snapshot | ADD E2E |
| B2 | Complete GAD-7 | Scale definitions/runtime tests | Canonical GAD count/range, no PHQ constant leakage | ADD E2E |
| B3 | Complete PCL-5 short flow | Scale definitions/runtime tests | Canonical PCL range and completion | ADD E2E |
| B4 | Ambiguous answer | `test_scale_answer_interpreter.py` | Clarification, unchanged item/waiting, no score | ADD E2E |
| B5 | Pause/resume after relaxation | `test_scale_state_boundary.py`, app engine relaxation tests | Runtime real unanswered item restored; no UI item owner | ADD E2E |
| B6 | Completed scale report | report and scale tests | Report reads runtime completion; no mutation | ADD E2E |
| C1 | Early explicit relaxation | `test_phase5_policy.py` | User request bypasses threshold; engine only executes approval | ADD E2E |
| C2 | Proactive relaxation at 7 rounds | Phase 5 boundary tests | No recommendation/media/allowance change | ADD E2E |
| C3 | Proactive relaxation at 8 rounds | Phase 5 policy tests | One recommendation and lifecycle event | ADD E2E |
| C4 | Second proactive recommendation | Phase 5 policy tests | Once-per-session rejection, explicit request still distinct | ADD E2E |
| C5 | Waiting scale blocks proactive | Phase 5 policy tests | Pending item unchanged; explicit pause path remains valid | ADD E2E |
| D1 | Explicit game | `test_game_service.py`, integration fakes | `RECOMMEND_GAME`, no REC tag authority, one media command | ADD E2E |
| D2 | Boredom | Phase 5 policy tests | Chat only, no game/media | ADD E2E |
| E1 | Explicit end in chat | app/policy tests | User signal wins; one direct end; no readiness/forced relaxation | ADD E2E |
| E2 | End during scale | session/policy tests | No fabricated score or item advance; partial report snapshot | ADD E2E |
| E3 | Positive feedback | Phase 5 policy tests | No end command | ADD E2E |
| E4 | End during relaxation media | `test_app_engine.py::TestEndDuringVideo` | Deferred end resumes after media without deadlock | ADD E2E |
| E5 | Report then farewell | report service/MainWindow paths | Persistence event precedes farewell generation/TTS | ADD E2E |
| F1 | First timeout choice | `test_app_engine.py::TestTimeLimitDecisions` | One choice, no automatic end | ADD E2E |
| F2 | Continue timeout suppression | app engine tests | No repeated choice in same session | ADD E2E |
| F3 | Timeout end choice | app engine/policy tests | One end path, no forced relaxation | ADD E2E |
| G1 | Sentence streaming | `test_phase7_delivery_boundary.py` | Sentence 1 UI/TTS before provider completion; ordered queue | ADD E2E |
| G2 | New turn cancels old | Phase 7 delivery tests | Old queue drain, stop hook, new current generation | ADD E2E |
| G3 | Stale callbacks | Phase 7 stale tests | No UI/TTS/history/DataManager side effects | ADD E2E |
| G4 | Delivered-only history | Phase 7 ledger tests | A retained, B/C excluded, generated tail diagnostic only | ADD E2E |
| G5 | Completed delivery once | Phase 7 ledger tests | Full visible response persisted exactly once | ADD E2E |
| G6 | Zero-visible cancellation | Phase 7 ledger tests | No phantom assistant entry | ADD E2E |
| H1 | Session A -> B | engine/runtime reset tests | No scale/relaxation/timeout/generation/report leakage | ADD E2E |
| H2 | Stale session callback | MainWindow generation paths | Session A auxiliary callback cannot affect B | ADD E2E |
| I1 | Router timeout/invalid JSON | agent timeout/protocol tests | Fallback proposal, one decision, normal delivery | ADD E2E |
| I2 | RAG unavailable | RAG/config tests | Bounded fallback after approved gate, no second authority | ADD E2E |
| I3 | 72B exception | LLM/pipeline tests | Pre-visible no history; post-visible prefix only | ADD E2E |
| I4 | Sentence TTS failure | Phase 7 fake TTS tests | Visible history unaffected, no replay/reorder | ADD E2E |
| I5 | Media failure | `test_game_service.py`, headless UI tests | Existing failure state only; no false completion | ADD E2E |
| I6 | Report failure | report/storage failure tests | Existing error presentation and no false report completion | ADD E2E |

## 5. Required recorder fields

The proposed test-only recorder should expose these explicit lists/properties;
the names are stable test vocabulary, not production contracts:

```python
router_proposals
policy_calls
turn_decisions
runtime_snapshots
accepted_answers
engine_commands
engine_events
rag_queries
llm_prompts
provider_chunks
generation_events
visible_sentences
tts_calls
tts_stop_calls
history_writes
data_manager_writes
report_events
media_events
```

Every entry that can be asynchronous must include `session_id` where the
current path exposes it and `generation_id` where it is participant-facing.
The recorder must preserve event order and support assertions that no event
appeared after cancellation or session reset.

## 6. Cross-scenario invariants

These are acceptance gates across the whole matrix, not optional per-scenario
extras:

1. No Router proposal contains an executable `requested_item` or `scale_score`.
2. Every accepted user turn has exactly one `TurnDecision`.
3. Every RAG query has a preceding decision with `needs_rag=True`; every
   `needs_rag=False` turn has zero production RAG calls.
4. Scale item progression and score storage appear only in ScaleRuntime
   snapshots; UI and SessionEngine traces are commands/projections.
5. Lifecycle transitions appear only as SessionEngine events; policy does not
   move lifecycle state by direct mutation.
6. 72B legacy END/REC/SCALE output, if deliberately injected for robustness,
   has no control effect.
7. Every UI/TTS/history callback has a generation ID or is explicitly
   classified as non-participant operator UI; no unscoped assistant delivery
   path is accepted.
8. `delivered_text` equals the visible participant-facing prefix and is the
   only assistant text written to conversation history/DataManager.
9. Reports read finalized delivered history and preserve report-before-farewell.
10. Session B starts with clean ScaleRuntime, SessionEngine, timeout, policy
    allowance, delivery, and report scopes.
11. No scenario imports or reaches `safety/resources/crisis_knowledge.json`.
12. A100/vLLM/STT/TTS provider construction and endpoint strings remain
    unchanged by the test-only implementation.

## 7. Planned Phase 8 files and ownership

These paths are proposed for the implementation after this freeze; they are
not present as Phase 8 changes yet:

| Path | Responsibility | Production authority impact |
|---|---|---|
| `tests/e2e/__init__.py` | Test package marker | None |
| `tests/e2e/fixtures.py` | Recorder, scripted providers, lifecycle cleanup | Test-only |
| `tests/e2e/test_phase8_conversation_scenarios.py` | A–H successful and recovery scenarios | Test-only |
| `tests/e2e/test_phase8_failure_scenarios.py` | I failures, cancellation, stale/reset races | Test-only |
| `docs/refactor/phase8_e2e_acceptance_implementation.md` | Final evidence record after implementation | Documentation-only |

Existing `tests/integration/fakes.py` may be extended only when its call
surface is shared by old integration tests and the new recorder. No broad
`git add .` is permitted; implementation must stage exact test/doc paths.

## 8. Freeze acceptance gate

Before Phase 8 implementation starts, confirm:

```text
Phase 1–7: FROZEN / ACCEPTED
Phase 8 design: COMPLETE LOCALLY
Phase 8 production/test implementation: NOT STARTED
working tree: clean before adding these two docs
baseline: 471 passed, 1 skipped, 0 failed
real deployment smoke: NOT RUN / environment unavailable
```

The next action after this docs-only freeze is the separately reviewed test
implementation commit:

```text
test: add complete end-to-end conversation scenario suite
```

No Phase 9 design or production work is included in this inventory.
