# Phase 7: cancellable sentence streaming TTS

> This document is the Phase 7 design-freeze artifact. It defines the
> implementation contract for the next production phase; it does not implement
> Phase 7.

## Status and freeze boundary

- Status: **formal specification only; Phase 7 production implementation is
  not started**.
- Date: 2026-08-15.
- Branch: `codex/a100-vllm-safety`.
- Accepted Phase 6 implementation:
  `b8d8ed1536130f235c7651eea758edd222725561`
  (`refactor: simplify prompts rag and response protocol`).
- Current design-freeze baseline:
  `7ef939e28b60101ae6b2063472ae155fab35f032`
  (Phase 6 implementation record and remote verification).
- Baseline command:
  `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe -m pytest tests -q`.
- Baseline result verified before this freeze: **450 passed, 0 failed,
  0 skipped** in 59.05 seconds.
- The freeze adds only this specification and the companion ownership
  inventory. It does not modify production Python, tests, model endpoints,
  TTS providers, STT, VAD, deployment profiles, or report schemas.

The companion file `phase7_delivery_cancellation_inventory.md` is part of this
freeze. Both files must be reviewed together before the implementation commit
`feat: enable cancellable sentence streaming tts`.

## 1. Objective

Phase 7 introduces one generation-scoped delivery contract from provider
streaming through the UI, sentence-level TTS, cancellation, and conversation
history. The target path is:

```text
user turn accepted
        |
        v
GenerationController creates generation_id
        |
        v
existing RouterProposal -> TurnPolicy -> exactly one TurnDecision
        |
        v
72B/vLLM/Ollama provider chunks
        |
        v
Generation-scoped text assembler
        |
        v
Chinese sentence segmenter
        |
        +--> SentenceReady(generation_id, seq, text) --> visible UI
        |
        +--> one cancellable TTS queue/playback worker
        |
        v
Delivery ledger and history finalization
```

The implementation must produce short, stable sentences for playback rather
than invoking TTS once for the whole answer or once per token. A new user turn
invalidates every unfinished delivery from the previous generation. Provider
architecture remains unchanged: the existing vLLM 72B endpoint (`:8000`), the
3B Router endpoint (`:8001`), Ollama development compatibility, FunASR, VAD,
and VoxCPM2/CosyVoice remain the same adapters.

## 2. Frozen authority boundaries

| Component | Phase 7 responsibility | Explicit prohibition |
|---|---|---|
| `RouterProposal` / 3B Router | Observation and advisory route only. | It cannot create, cancel, or sequence a generation. |
| `TurnPolicy` | Sole per-turn action authority. | Streaming and TTS cannot become a second policy decision. |
| `TurnDecision` | Already-approved action and `needs_rag` value consumed by the turn. | A sentence callback cannot alter the action, scale, session, or RAG gate. |
| `ScaleRuntime` | Sole scale state/scoring owner. | Delivery cancellation cannot advance, undo, or score a scale item. |
| `SessionEngine` | Sole session-lifecycle writer. | It is not replaced by a delivery worker and does not own sentence policy. |
| Generation/delivery layer | Allocates generation IDs, assembles text, segments sentences, cancels stale work, and records delivery. | It is not a business-policy or lifecycle authority. |
| `72B` provider | Emits language chunks for the supplied decision/context. | It is not asked to choose actions and does not emit a machine control protocol. |
| Sentence segmenter | Emits deterministic `SentenceReady` units from generated text. | It does not infer intent, score, end, recommend, or retrieve. |
| TTS queue/worker | Synthesizes and plays accepted sentence units in sequence. | It cannot update `TurnDecision`, `ScaleRuntime`, or `SessionEngine`. |
| `MainWindow` / `ChatPanel` | Renders generation-scoped UI events and sends user/device commands. | It cannot let stale callbacks mutate the visible current turn. |
| Conversation history / `DataManager` | Persists the delivered participant-facing projection exactly once per turn. | Raw generated tails and unplayed audio are not dialogue facts. |

Phase 1 safety isolation and all Phase 2–6 boundaries remain in force. In
particular, no Phase 7 code may reintroduce crisis flow, Guard access, Router
authority, scale-state migration, SessionEngine authority migration, or a new
RAG decision path.

## 3. Frozen generation contract

### 3.1 `generation_id`

`generation_id` is an opaque, monotonically increasing identifier for one
assistant delivery turn. The delivery layer allocates it before provider
generation begins, after the user turn has been accepted. It is carried on all
events and callbacks below. It is not a session lifecycle state and is not
interpreted by `TurnPolicy`, `ScaleRuntime`, or `SessionEngine`.

There is exactly one allocator and one current-generation record. MainWindow
may request `start_generation`/`cancel_generation`, but after implementation it
must not maintain a second independent stale integer. A compact immutable
record is sufficient:

```python
GenerationRecord(
    generation_id: int,
    cancelled: bool,
    generated_text: str,
    delivered_text: str,
    next_sentence_seq: int,
)
```

The record may be implemented as a class or dataclass. Its fields are delivery
bookkeeping, not business policy.

### 3.2 Text meanings

- `generated_text`: every content character accepted from the provider for the
  generation, including a tail that was later cancelled or failed. It is
  diagnostic/telemetry data only.
- `delivered_text`: the ordered text that has entered the current visible AI
  chat bubble and has not been retracted. This is the assistant text committed
  to new conversation history.
- `spoken_text`/`tts_text`: the normalized sentence payload handed to the TTS
  adapter. Audio synthesis/playback progress is tracked independently from
  `delivered_text`.
- `cancelled`: an idempotent generation state. Cancellation makes all future
  work for that ID stale; it does not rewrite already-delivered text.
- `stale`: any event whose ID is not the current non-cancelled generation. A
  stale event is dropped without UI, TTS, history, scale, session, or RAG side
  effects.

### 3.3 Events

The implementation must use generation-scoped event values (dataclasses or an
equivalent typed structure) with these semantics:

```python
GenerationStarted(generation_id)
TextChunk(generation_id, text)
SentenceReady(generation_id, seq, text)
SentenceDelivered(generation_id, seq, text)
AudioStarted(generation_id, seq)
AudioFinished(generation_id, seq, ok)
GenerationCancelled(generation_id, reason)
GenerationFinished(generation_id, generated_text, delivered_text)
```

`TextChunk` is an internal assembly event. `SentenceReady` is the only unit
that may enter the TTS queue. `SentenceDelivered` means the sentence was
appended to the visible chat bubble, not that the speaker completed it.
`AudioStarted`/`AudioFinished` are progress telemetry and must not affect
conversation policy. A callback may be omitted only when the corresponding
side effect cannot happen; it may never be emitted without a generation ID.

## 4. Frozen streaming and sentence semantics

### 4.1 Provider stream

The existing provider iterators (`LLMService.chat`, the vLLM-compatible
service, and `VLLMOpenAIClient.stream_messages`) remain the transport. The
delivery layer consumes each content chunk once, appends it to
`generated_text`, and forwards it to the assembler with the generation ID.
The provider may be closed or abandoned on cancellation when its SDK permits
that operation. Provider retry/fallback behavior remains unchanged except that
late output must pass the stale check.

The pipeline must no longer buffer an entire response merely to make TTS
possible. It may retain a bounded generated-text buffer for diagnostics and a
small sentence buffer for segmentation. A provider chunk is never itself a
TTS unit.

### 4.2 Sentence boundaries

The segmenter emits a sentence when one of these conditions is met:

1. a stable Chinese sentence terminator appears: `。`, `！`, `？`, or `……`;
2. a configured maximum character bound is reached at a safe punctuation or
   whitespace boundary; or
3. the generation ends, is cancelled, or the bounded flush timer expires, in
   which case the remaining non-empty text is flushed once.

Commas and ordinary short pauses do not create a new TTS call by themselves.
The segmenter must avoid splitting inside a paired quote/parenthesis when the
closing punctuation has not arrived, and must not emit an empty sentence.
Repeated punctuation (`！！`, `？？`, `……`) belongs to the same boundary.

The numeric tuning values are intentionally selected only after measuring the
current VoxCPM2/CosyVoice call behavior. The implementation must expose named
configuration for `max_chars`, `max_wait_ms`, and `min_stable_chars`, choose
bounded values, record the measured values in the implementation record, and
test them. The freeze fixes the invariants above rather than guessing a
provider-specific latency number in this document. A finite bound and a
bounded flush are mandatory; an unbounded wait or token-by-token TTS is not
permitted.

### 4.3 TTS queue and worker

There is one ordering point for sentence playback. The worker accepts only
`SentenceReady` values for the current generation, preserves increasing
`seq`, and owns the queue drain on cancellation. The worker calls the existing
TTS adapter with one complete sentence at a time:

```python
tts.generate_and_play(sentence.text)
```

The adapter remains responsible for model-specific audio streaming. Phase 7
does not replace VoxCPM2's or CosyVoice's internal audio chunking with token
TTS, and it does not change model loading, voice prompts, sample rates, or
device selection. `stop_playing()` is a best-effort interruption hook; the
generation cancellation event is the authoritative stale check.

## 5. Frozen cancellation and stale-callback rules

### 5.1 Starting a new turn

When a new user turn is accepted:

1. mark the previous current generation cancelled (idempotently);
2. signal the previous provider iterator and TTS worker;
3. call the existing TTS stop hook as a best-effort audio interrupt;
4. drain or mark stale all queued `SentenceReady` values from that ID; and
5. allocate the next generation ID before new provider work starts.

The new turn is not delayed until the old provider thread exits. Old threads
may finish in the background, but every result must fail the current-generation
check before it can cause a side effect.

### 5.2 Required stale checks

The ID/cancellation check is required at all of these boundaries:

```text
provider chunk received
assembler append/flush
sentence-ready emission
UI queue insertion
UI append/finish callback
TTS queue insertion
before synthesis
after synthesis / before playback
playback-completed callback
history finalization
farewell/greeting replacement callback
```

The check must be performed again after every blocking or asynchronous
operation. A stale callback is discarded; it cannot append UI text, play audio,
write history, invoke `DataManager`, mutate `ScaleRuntime`, submit an
`SessionEngine` command, or call RAG.

### 5.3 Cancellation granularity

Cancellation stops future sentences and best-effort stops the currently
playing sentence. It does not retract text already made visible. A partial
sentence that was never emitted as `SentenceReady` is not delivered or written
to history. If a sentence was emitted and the UI appended it before
cancellation, that sentence remains in `delivered_text`; its audio may be
marked interrupted separately.

## 6. Frozen UI, history, and delivery semantics

### 6.1 UI

`ChatPanel` may continue to render incremental text, but all UI queue entries
carry `generation_id`. `start_ai_message`, `stream_text`, and
`finish_streaming` become generation-aware events rather than unscoped global
messages. `replace_greeting` and `replace_last_system` use the same contract;
they cannot replace a bubble after their generation is stale.

The UI-visible append is the delivery commit point. The delivery ledger must
record each accepted sentence once, in sequence, and must reject duplicate or
out-of-order commits for the same generation.

### 6.2 Conversation history and DataManager

New history stores normalized `delivered_text`, not the raw provider response.
The assistant message is finalized exactly once when the generation completes
or is cancelled. A cancelled tail that never entered the visible chat is
excluded. Existing `DataManager.save_assistant_message` remains a persistence
adapter; the Phase 7 delivery owner decides what text is passed to it. Audio
completion is recorded separately and must not gate history finalization.

`generated_text` may be logged or sent to bounded debug telemetry under the
existing logging policy. It is never automatically copied into the next LLM
conversation history as if the participant heard it.

### 6.3 Farewell and auxiliary generated text

Opening greetings, post-relaxation greetings, fill-info prompts, and the
SessionEngine-approved farewell are also generation-scoped deliveries. The
report-first ordering and lifecycle authority from Phase 4–5 remain unchanged:
SessionEngine accepts the end, MainWindow performs report/UI work, and the
farewell uses the same stale/cancellation checks. Phase 7 does not change when
an end is approved or what a report contains.

## 7. Compatibility and failure handling

- Phase 6's plain-text `ResponseBuilder` contract remains the input to the
  assembler. Narrow defensive removal of historical tags/prosody markers may
  remain, but a legacy tag cannot control a generation or lifecycle.
- Empty provider output uses the existing safe fallback as one generation-scoped
  sentence. It is delivered/history-committed only if it passes the same stale
  checks.
- Provider failure after visible sentences finalizes only the visible prefix
  and records the failure. Provider failure before any visible sentence does
  not create a phantom assistant history entry.
- TTS synthesis/playback failure does not erase visible history. It records
  audio failure and permits the worker to stop or continue according to the
  generation cancellation state.
- Shutdown cancels the current generation, stops the queue/worker, and rejects
  all late callbacks before process teardown.

## 8. Required implementation tests

The implementation must add boundary/unit/scenario tests before production
changes are accepted. At minimum they must prove:

1. each generation ID is unique/monotonic and only one is current;
2. provider chunks are assembled once and no token becomes an individual TTS
   call;
3. `。`, `！`, `？`, and `……` produce stable sentence boundaries;
4. max-character and bounded-flush rules emit remaining text exactly once;
5. a new user turn cancels the old generation and stops future sentence work;
6. stale provider chunks, sentence events, UI events, TTS callbacks, and
   history finalizers have no side effect;
7. UI delivery order is `seq` order and duplicate/out-of-order events are
   rejected;
8. `delivered_text` contains only visible text, while an unplayed generated
   tail is excluded from history;
9. a partially played sentence leaves visible history intact and records audio
   interruption separately;
10. TTS failure, provider failure, empty output, and shutdown are idempotent;
11. opening/post-relaxation/farewell paths carry generation IDs and stale
    replacements are dropped;
12. Phase 1–6 authority tests remain green: no Router/72B/TTS/UI callback can
    mutate TurnDecision, ScaleRuntime, SessionEngine, or RAG authority; and
13. A100/vLLM/STT/TTS provider construction and ports remain unchanged.

## 9. Implementation sequence after this freeze

The next production commit must use exactly:
`feat: enable cancellable sentence streaming tts`.

Implementation order is frozen as follows:

1. Add red tests for generation identity, sentence segmentation, stale events,
   queue ordering, delivery history, and provider preservation.
2. Introduce the smallest delivery-layer generation record/token and migrate
   existing MainWindow generation markers to it; do not create a policy or
   lifecycle authority.
3. Add the assembler and sentence segmenter with measured TTS configuration;
   keep provider chunk transport intact.
4. Add the single TTS queue/worker and generation checks at every blocking
   boundary; retain adapter `generate_and_play`/`stop_playing` surfaces.
5. Make UI events, greeting/farewell callbacks, and history finalization
   generation-aware; store only delivered text.
6. Run affected tests, the full suite, `git diff --check`, and the local smoke
   checks. Record real A100/STT/TTS availability honestly.

## 10. Explicit non-goals

Phase 7 does not:

- change RouterProposal, TurnPolicy, TurnDecision, `needs_rag`,
  ScaleRuntime, SessionEngine, or Phase 5 business rules;
- add a second action/lifecycle authority or move state into a TTS worker;
- redesign vLLM/Ollama, FunASR, VAD, VoxCPM2, CosyVoice, A100 ports, or model
  placement;
- reintroduce crisis/Guard runtime or access `safety/resources`;
- change RAG allowlists or prompt/response control semantics from Phase 6;
- implement token-by-token TTS, a new provider, or a new prosody protocol;
- change report policy, scale scoring, relaxation/game/end/timeout eligibility;
  or
- begin Phase 8 or any later response/history redesign.

Phase 7 is complete only when one generation-scoped delivery contract explains
every live chunk, sentence, UI append, TTS call, cancellation, callback, and
history write, while the five existing authority questions remain unchanged:

```text
TurnPolicy       -> who decides this turn's action
ScaleRuntime     -> who owns scale state and score acceptance
SessionEngine    -> who owns session lifecycle
TurnDecision     -> whether RAG is allowed
72B              -> language only; no control protocol
```

The local runtime smoke status remains environment-dependent. The current
development machine can run the Python test suite, but a real A100/vLLM/STT/
TTS deployment is **NOT RUN / environment unavailable** unless separately
verified during implementation.
