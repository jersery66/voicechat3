# Phase 8: complete end-to-end conversation scenario acceptance

> This document is the Phase 8 design-freeze artifact. It defines the final
> system-level acceptance suite; it does not add tests or change production
> code.

## Status and freeze boundary

- Status: **formal specification only; Phase 8 production/test implementation
  is not started**.
- Date: 2026-08-15.
- Branch: `codex/a100-vllm-safety`.
- Phase 7 production implementation:
  `4858cd90debdd1d68df200f1c5394a288df26098`
  (`feat: enable cancellable sentence streaming tts`).
- Design baseline: `66fdb7d25964f5b49d142643eb8178eca1e4af71`
  (`docs: correct phase 7 implementation sha`).
- Baseline command used for this freeze:
  `$env:PYTHONPATH='E:\Anaconda\envs\voice_chat\Lib\site-packages'; E:\Anaconda\python.exe -m pytest tests -q`.
- Baseline result: **471 passed, 1 skipped, 0 failed**. The single skip is
  the optional offscreen Qt test when `PySide6.QtWidgets` cannot load in the
  current environment. No real A100/vLLM, FunASR, VoxCPM2/CosyVoice, or
  microphone smoke was claimed; those remain **NOT RUN / environment
  unavailable**.
- This freeze adds only this specification and the companion scenario
  inventory. It does not modify Python source, tests, configuration, model
  endpoints, deployment scripts, prompts, RAG data, TTS providers, or report
  schemas.

The companion file `phase8_scenario_inventory.md` is part of this freeze.
Both documents must be reviewed together before the implementation commit:

```text
test: add complete end-to-end conversation scenario suite
```

## 1. Goal

Phase 8 is the final integration acceptance layer for Phases 1–7. It must
prove that one participant turn can travel through the already-frozen
authority chain and return as participant-facing text/audio/history/report
without creating a second owner at any boundary:

```text
user text or STT transcript
        |
        v
RouterProposal (advisory)
        |
        v
TurnPolicy -> exactly one TurnDecision
        |
        +--> ScaleRuntime (scale state and score acceptance)
        +--> SessionEngine (session lifecycle commands/events)
        +--> RAG only when TurnDecision.needs_rag is true
        |
        v
72B language realization
        |
        v
generation-scoped sentence delivery
        |
        +--> visible ChatPanel text
        +--> one ordered TTS sentence queue
        +--> delivered-history/DataManager commit
        +--> report reader at session end
```

The suite is an acceptance suite, not a new runtime architecture. It should
use real policy, runtime, engine, pipeline, delivery, and report orchestration
where those components are deterministic, and scripted fakes at external
model/audio/device seams. A passing test must demonstrate a complete business
scenario and the authority evidence for that scenario; a final spoken string
alone is insufficient.

## 2. Frozen authority chain

| Component | Phase 8 assertion | Forbidden test shortcut |
|---|---|---|
| `RouterProposal` / Router | Supplies observations and an optional action suggestion. | A scenario must not treat Router `item`, `score`, or a raw tag as executable truth. |
| `TurnPolicy` | Produces the one executable `TurnDecision` for the accepted user turn. | No test may invoke a second policy or infer an action from 72B output. |
| `TurnDecision` | Carries the approved action and the sole `needs_rag` gate. | UI, RAG, TTS, or report code must not replace it. |
| `ScaleRuntime` + `ScaleAnswerInterpreter` | Owns current item, waiting state, pause/resume, valid score acceptance, and completion. | Router, 72B, UI, and SessionEngine cannot own a writable item/score copy. |
| `SessionEngine` | Owns lifecycle state, end/deferred-end, relaxation media transitions, and timeout markers. | Pipeline and UI may send commands or consume events, but may not decide lifecycle policy. |
| Production RAG | Provides curated context only after `needs_rag=True`. | RAG must not run a second intent/action decision or reach `safety/resources`. |
| `72B` provider | Realizes supplied context/decision as plain participant-facing language. | It must not choose scale/game/relaxation/end, score an item, or emit a required control protocol. |
| Phase 7 delivery layer | Owns generation IDs, sentence order, cancellation, visible delivery, and delivered-history finalization. | It cannot decide business action, mutate scale state, or submit lifecycle policy. |
| `MainWindow` / `ChatPanel` | Sends commands and renders generation-scoped UI/device output. | UI state is not a second scale, lifecycle, or delivery authority. |
| `DataManager` / report readers | Persist and read the delivered participant-facing projection. | Generated-but-undelivered tails cannot become dialogue facts. |

The Phase 1 crisis/safety isolation and all Phase 2–7 boundaries remain in
force. Phase 8 must not reintroduce crisis runtime, Guard access, Router
authority, scale-state shadow ownership, SessionEngine migration, Phase 5
policy changes, Phase 6 control tags, or a new TTS protocol.

## 3. Test-harness contract

### 3.1 Real and fake boundaries

The implementation may add test-only helpers under `tests/e2e/` and extend
the existing scripted fakes in `tests/integration/fakes.py`. The preferred
fixture uses:

```text
real ConversationPipeline
real TurnPolicy / RouterProposal / TurnDecision
real ScaleRuntime / ScaleAnswerInterpreter
real SessionEngine command/event facade
real GenerationController / SentenceSegmenter / DeliveryLedger / TTS queue
scripted Router, 72B stream, RAG, STT, TTS, video, DataManager, report sink
```

The fake provider interfaces must record every call and payload. The recorder
must capture, in order:

```text
user input and session ID
RouterProposal
TurnPolicy invocation and TurnDecision
TurnStateSnapshot before/after the decision
ScaleRuntime snapshots and accepted answers
SessionEngine commands and emitted lifecycle events
TurnDecision.needs_rag and RAG queries
provider chunks and generation IDs
SentenceReady / visible UI commits / TTS calls
generated_text / delivered_text
history and DataManager writes
report/farewell ordering
```

The recorder is an assertion aid only. It must not be imported by production
modules or become a new authority. Every fixture must shut down the pipeline,
delivery worker, and SessionEngine in `finally`/fixture teardown so tests do
not depend on thread timing from another scenario.

### 3.2 Deterministic scenario driving

Each scenario must provide explicit scripted inputs and a deterministic clock
or event barrier for asynchronous work. It must not use sleeps as the primary
proof of ordering. A scenario may use a bounded polling helper only after an
observable event has been emitted. External providers are never contacted by
the acceptance suite.

The tests should be organized as two layers:

1. `tests/e2e/test_phase8_conversation_scenarios.py` for successful multi-turn
   business scenarios and authority traces.
2. `tests/e2e/test_phase8_failure_scenarios.py` for provider, media, report,
   timeout, cancellation, reset, and stale-callback paths.

The exact test names may vary, but each inventory ID below must map to at
least one named test and one trace assertion.

## 4. Scenario acceptance contract

### A. Ordinary conversation and RAG

1. **A1 — ordinary chat without RAG**
   - Send a normal text turn with `TurnDecision.CHAT` and
     `needs_rag=False`.
   - Assert exactly one RouterProposal and one TurnDecision, zero RAG queries,
     a generation-scoped plain response, ordered visible delivery, and one
     delivered assistant history entry.
   - Assert the 72B prompt contains the supplied conversational context but no
     request to make a new business decision.

2. **A2 — ordinary chat with approved RAG**
   - Script `needs_rag=True` for a wording that requires curated support
     context.
   - Assert exactly one production RAG query, the query reaches the 72B
     context, and no RAG-side action/intent classifier is called.

3. **A3 — psychology keyword with `needs_rag=False`**
   - Use psychology-related words while the authoritative decision explicitly
     disables retrieval.
   - Assert no RAG call, no keyword override, and otherwise normal chat.

4. **A4 — simple wording with `needs_rag=True`**
   - Use a short non-keyword query while the authoritative decision enables
     retrieval.
   - Assert retrieval occurs because of the decision, not because of a RAG
     heuristic.

### B. Scale administration

5. **B1 — complete PHQ-9 flow**
   - Start PHQ-9, accept every valid answer through
     `ScaleAnswerInterpreter`, and continue until the canonical definition
     marks the scale complete.
   - Assert Router never supplies an executable item or score, the decision
     sequence is `START_SCALE` then `CONTINUE_SCALE`, and every accepted score
     is validated by `ScaleRuntime` against PHQ-9's 0–3 range.
   - Assert 72B only verbalizes the runtime-selected question and the final
     report reads the completed runtime snapshot.

6. **B2 — complete GAD-7 flow**
   - Repeat the same path with GAD-7 and prove its canonical item count and
     valid score range are used rather than PHQ-9 constants.

7. **B3 — complete PCL-5 short flow**
   - Run the registered PCL-5 definition through all items exposed by the
     current product flow and assert its 0–4 score range and completion data.

8. **B4 — ambiguous answer requests clarification**
   - While `waiting_for_answer=True`, submit phrases such as `有时候吧` or
     `还行` that do not map reliably to a legal score.
   - Assert the decision remains scale continuation, the runtime item and
     waiting flag are unchanged, no answer is recorded, and the next response
     asks for clarification without silently advancing.

9. **B5 — pause and resume**
   - Pause a scale, perform an approved relaxation transition, then continue
     the session.
   - Assert the runtime snapshot restores the actual unanswered item, not a
     UI-cached item number, and no duplicate/phantom answer is written.

10. **B6 — completed scale reaches report**
    - Finish a scale and run the existing report path.
    - Assert report data is derived from `ScaleRuntime`'s completed snapshot,
      delivered text is the only conversation projection, and no report reader
      mutates scale state.

### C. Relaxation policy and lifecycle

11. **C1 — user-requested relaxation early**
    - Request relaxation before the proactive threshold.
    - Assert `TurnPolicy` approves `RECOMMEND_RELAXATION` without the minimum
      round gate, and `SessionEngine` only executes the approved command.

12. **C2 — proactive relaxation below eight rounds**
    - Provide a proactive candidate at seven rounds.
    - Assert policy returns chat/continuation, no media command is submitted,
      and no allowance is consumed.

13. **C3 — proactive relaxation at or above eight rounds**
    - Provide a candidate at eight rounds with no pending scale answer.
    - Assert one approved recommendation, one lifecycle transition, and the
      allowance marker is set only through the existing authority path.

14. **C4 — proactive recommendation is once per session**
    - Attempt a second proactive recommendation after the first one.
    - Assert it is rejected while an explicit user request remains independently
      eligible.

15. **C5 — waiting scale answer blocks proactive interruption**
    - Set a real pending scale item and send a proactive relaxation candidate.
    - Assert no recommendation interrupts the item; an explicit user request
      may still pause it according to Phase 5 policy.

### D. Game policy

16. **D1 — explicit game request**
    - Send `我想玩个游戏` and a matching Router proposal.
    - Assert `RECOMMEND_GAME` is approved, the engine/UI execute only that
      approved transition, and delivery contains no required `REC_*` tag.

17. **D2 — boredom is not an implicit game request**
    - Send `我好无聊` without an explicit request.
    - Assert policy returns ordinary chat and no game command/media starts.

### E. Explicit end and deferred lifecycle

18. **E1 — explicit end in ordinary chat**
    - Send `结束`/`不想聊了` and a conflicting non-end Router proposal.
    - Assert explicit user signal wins in `TurnPolicy`, no scale completion or
      forced relaxation is inserted, and `SessionEngine` receives one direct
      end command.

19. **E2 — explicit end while a scale is active**
    - End during a waiting item.
    - Assert no answer is fabricated, ScaleRuntime is not advanced, and the
      report/finalization path sees the actual partial snapshot.

20. **E3 — positive feedback is not end**
    - Send `好多了`/`轻松了`.
    - Assert policy remains chat/continuation and no end command is emitted.

21. **E4 — end during relaxation media**
    - Start approved media, request end while video is playing, then emit
      media completion.
    - Assert SessionEngine defers and resumes the already-approved end without
      inventing another relaxation or deadlocking.

22. **E5 — report-before-farewell**
    - Complete an end flow with a participant-facing farewell.
    - Assert report/PDF persistence is attempted and confirmed before farewell
      generation/TTS, and a stale farewell cannot replace newer UI text.

### F. Timeout

23. **F1 — one timeout choice**
    - Reach the time limit once.
    - Assert SessionEngine emits one continue/end choice and the turn itself
      does not force an end.

24. **F2 — continue suppresses repeated timeout prompts**
    - Choose continue, then advance time through the limit again.
    - Assert no second choice is emitted in the same session.

25. **F3 — timeout end choice follows explicit lifecycle path**
    - Choose end from the timeout dialog.
    - Assert the engine executes end once and policy/LLM cannot add forced
      relaxation or an extra timeout prompt.

### G. Phase 7 sentence delivery and cancellation

26. **G1 — two sentences stream before provider completion**
    - Make the scripted 72B stream yield sentence one, pause, then sentence
      two.
    - Assert sentence one reaches UI and the single TTS queue before sentence
      two/provider completion; TTS order is 0 then 1.

27. **G2 — new turn cancels the old generation**
    - Start generation 100, make sentence A visible, then start turn 101.
    - Assert generation 100 is cancelled, queued B/C are discarded,
      `stop_playing()` is attempted, and generation 101 is the only current
      generation.

28. **G3 — stale callbacks have no side effects**
    - Deliver late provider chunks, SentenceReady events, UI events, TTS
      completion, and history callbacks from generation 100 after 101 starts.
    - Assert none mutates the current bubble, calls TTS, appends history, or
      writes DataManager.

29. **G4 — delivered history excludes cancelled tail**
    - Generate A+B+C, visibly commit A, then cancel before B/C delivery.
    - Assert history and DataManager contain A exactly once, while diagnostic
      generated text may retain A+B+C.

30. **G5 — complete generation finalizes once**
    - Complete a multi-sentence generation without cancellation.
    - Assert full visible text is persisted once and duplicate finalization is
      a no-op.

31. **G6 — zero-visible cancellation has no phantom assistant turn**
    - Cancel before the first sentence reaches UI.
    - Assert no assistant history/DataManager entry is created.

### H. Session reset and isolation

32. **H1 — session A to session B reset**
    - Complete session A, start session B, and send a new turn.
    - Assert ScaleRuntime active/answers/completed state, relaxation allowance,
      timeout markers, delivery generations, and report/session identifiers do
      not leak across sessions.

33. **H2 — stale session callback rejection**
    - Let an auxiliary greeting, post-relaxation response, or farewell from
      session A arrive after session B starts.
    - Assert it cannot replace session B UI, play audio, or write session B
      history.

### I. Failure and recovery paths

34. **I1 — Router timeout/invalid response**
    - Script timeout and malformed JSON independently.
    - Assert the documented fallback proposal/policy decision is used once and
      the ordinary turn still has generation-scoped delivery.

35. **I2 — RAG unavailable**
    - Make curated RAG raise/return unavailable after `needs_rag=True`.
    - Assert the turn continues with bounded fallback context and no second
      RAG decision or unrelated action.

36. **I3 — 72B generation exception**
    - Fail the provider before and after one visible sentence.
    - Assert no phantom history before visibility; after visibility only the
      visible prefix is finalized and the failure is recorded.

37. **I4 — one sentence TTS failure**
    - Fail TTS for sentence one or two.
    - Assert visible history remains correct, generation bookkeeping remains
      exactly-once, and the queue does not replay or reorder sentences.

38. **I5 — media failure**
    - Fail relaxation/game media execution.
    - Assert lifecycle/report state records the existing failure behavior and
      does not mark an unplayed intervention as completed.

39. **I6 — report failure**
    - Fail report/PDF persistence at the existing report boundary.
    - Assert the current failure presentation is preserved and farewell is not
      claimed as report-complete.

## 5. Required authority assertions for every scenario

Every scenario that crosses a boundary must assert the applicable subset of
the following trace, not only its final UI text:

```text
1. The user/STT payload was accepted once.
2. One RouterProposal was observed (or the documented fallback was recorded).
3. TurnPolicy was invoked once for that accepted turn.
4. Exactly one TurnDecision was attached to the pipeline result.
5. If a scale is involved, ScaleRuntime owns the item/answer snapshot.
6. If lifecycle changes, SessionEngine owns the command/event transition.
7. RAG was called iff TurnDecision.needs_rag is true.
8. 72B output changed language only; tags/text cannot change the action.
9. Every participant-facing async event carries the current generation ID.
10. Visible text, not generated tail or audio completion, defines history.
11. Report reads finalized delivered history and does not mutate authority state.
```

The test must also assert negative authority evidence where relevant:

```text
Router item/score is not executable.
72B END/REC/SCALE text has no control effect.
RAG keyword heuristics cannot override needs_rag.
UI shadow fields cannot advance ScaleRuntime or SessionEngine.
Stale generation callbacks cannot mutate UI/TTS/history/DataManager.
```

## 6. Regression and deployment preservation

The Phase 8 suite must run after the existing full suite and must not weaken
or delete Phase 1–7 tests. At minimum it must keep these boundary families
green:

```text
tests/test_crisis_runtime_boundary.py
tests/test_turn_authority*.py
tests/test_scale_*boundary.py
tests/test_phase4_lifecycle_boundary.py
tests/test_phase5_policy_boundary.py
tests/test_phase6_*boundary.py
tests/test_phase7_delivery_boundary.py
tests/integration/test_pipeline_e2e.py
```

The suite must not change or mock away the deployment contracts for:

```text
vLLM 72B :8000
vLLM 3B Router :8001
Ollama development compatibility
FunASR / VAD
VoxCPM2 / CosyVoice
A100 launch/profile configuration
```

The acceptance run is local/fake-backed. Real deployment smoke remains an
explicit status field and must be reported as **NOT RUN / environment
unavailable** when the current machine cannot provide the services.

## 7. Implementation sequence after this freeze

The next implementation work is test-only unless an independently reviewed,
minimal integration defect must be fixed. It must use the following sequence:

1. Add the deterministic recorder and scripted scenario fixtures under
   `tests/e2e/`; run the new tests red where the scenario contract is not yet
   represented.
2. Add successful scenarios A–H in inventory order, keeping each test tied to
   a single authority trace and ensuring all asynchronous resources are shut
   down.
3. Add failure scenarios I and stale-callback race coverage; use explicit
   barriers rather than sleeps for ordering proofs.
4. Run all Phase 1–7 boundary tests plus the full suite. No existing failure
   may be hidden with a broad skip or weakened assertion.
5. Run `git diff --check`, inspect the exact file list, and record the real
   local smoke result. Do not add Phase 9 or response-protocol redesign work.

The production/test implementation commit must be:

```text
test: add complete end-to-end conversation scenario suite
```

The implementation record must state scenario counts, focused/full results,
the one environment skip if still present, deployment preservation, and
whether real A100/STT/TTS smoke was run. It must not claim hardware success
from fake-backed scenarios.

## 8. Explicit non-goals

Phase 8 does not:

- redesign `TurnPolicy`, `TurnDecision`, `ScaleRuntime`, or `SessionEngine`;
- add Router fields, a second policy, a second lifecycle writer, or a second
  RAG gate;
- alter scale definitions, scoring ranges, intervention thresholds, explicit
  end semantics, or timeout rules;
- replace the Phase 7 generation/delivery contract or begin a Phase 8/9
  response-history redesign;
- add sentence-level TTS features beyond the Phase 7 contract;
- change vLLM/Ollama, FunASR, VAD, VoxCPM2, CosyVoice, A100 endpoints, or
  launch scripts;
- reintroduce crisis/Guard runtime or access `safety/resources`;
- load real models, contact Ollama/vLLM, use microphones, or claim hardware
  smoke from test doubles; or
- start a later phase.

Phase 8 is accepted only when the complete scenario suite proves the following
answers remain unique:

```text
TurnPolicy / TurnDecision -> who decides this turn's action
ScaleRuntime               -> who owns scale state and accepted scores
SessionEngine              -> who owns session lifecycle
TurnDecision.needs_rag     -> whether production RAG runs
72B                        -> how the approved action is expressed
Generation delivery layer  -> what the participant actually receives
History/report             -> what was actually delivered, exactly once
```
