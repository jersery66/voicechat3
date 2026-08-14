# Phase 7 delivery and cancellation ownership inventory

> Companion inventory for `phase7_streaming_tts_spec.md`. This is a read-only
> baseline of the Phase 6 code. It does not implement a generation contract or
> change any production owner.

## 1. Inventory status and method

- Status: **complete for Phase 7 design freeze; production implementation is
  not started**.
- Date: 2026-08-15.
- Branch: `codex/a100-vllm-safety`.
- Baseline HEAD: `7ef939e28b60101ae6b2063472ae155fab35f032`.
- Working tree at inventory time: clean before adding these two documents.
- Baseline command:
  `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe -m pytest tests -q`.
- Baseline result: **450 passed, 0 failed, 0 skipped** in 59.05 seconds.
- Search scope: tracked production, adapter, UI, data, inference, app, and
  test files; documentation/graph output was excluded from runtime findings.
- Search terms covered: `_pipeline_generation`,
  `_pipeline_cancel_generation`, `cancel`, `cancelled`, `stop_playing`,
  `generate_and_play`, `stream_text`, `finish_streaming`,
  `start_ai_message`, `conversation_history`, `add_assistant`,
  `save_message`, `save_conversation`, `DataManager`, `tts_text`,
  `spoken_text`, `generated_text`, `threading.Thread`, `processing_queue`,
  `stream_generate`, `yield chunk`, `replace_greeting`,
  `replace_last_system`, and farewell/TTS paths.

The inventory distinguishes an existing *generation-like stale-result token*
from the required Phase 7 *delivery generation contract*. The former exists in
`MainWindow`; the latter does not yet exist end to end.

## 2. Current live path in one view

```text
MainWindow._on_text_submitted / _on_record_stopped
  -> increment _pipeline_generation
  -> daemon _run_pipeline(text, generation)
  -> ConversationCoordinator / ConversationPipeline.execute
  -> LLMService.chat or VLLMCompatibleLLMService.chat
  -> _stream_llm joins every chunk into generated_text
  -> ResponseBuilder normalizes the complete response
  -> emit start_ai_message / stream_text / finish_streaming
  -> DataManager.save_assistant_message(clean_spoken)
  -> ThreadPoolExecutor.submit(_play_tts, tts_text)
  -> TTSService.generate_and_play(whole text)
```

The current UI callback filter checks the integer token only when the pipeline
places a callback into `processing_queue` and when `_run_pipeline` returns. It
does not travel through provider chunks, sentence work, TTS calls, playback
callbacks, greetings, farewell generation, or history finalization. The
current TTS implementations stream *audio chunks internally*, but they receive
one complete text string and have no Phase 7 sentence queue or generation ID.

## 3. Ownership inventory

### 3.1 Generation lifecycle and cancellation

| ID | Current entry/state | Current owner and mutation | Current risk/boundary | Phase 7 target owner | Disposition | Acceptance proof |
|---|---|---|---|---|---|---|
| G1 | `ui/main_window.py:_pipeline_generation` | `MainWindow` increments before text submission and after voice recording; passes a bare integer to `_run_pipeline`. | It is a stale-result token, not an object shared by LLM/UI/TTS/history. It has no cancellation event or delivery ledger. | One delivery-layer `GenerationController`/record allocates an opaque monotonic ID per assistant turn. | **MIGRATE**; remove duplicate raw counter after cutover. | Two rapid turns create distinct IDs; every event carries the correct ID. |
| G2 | `ui/main_window.py:_pipeline_cancel_generation` and `_cancel_active_pipeline()` | `MainWindow` sets a numeric cutoff and calls `tts_service.stop_playing()`. | A cutoff only affects selected UI/pipeline paths; it cannot cancel provider iteration, queued TTS, or late history callbacks. | Idempotent `cancel_generation(id, reason)` sets a cancellation event and invalidates all downstream work. | **MIGRATE**; retain `stop_playing()` only as best-effort adapter hook. | Cancelled generation emits no later UI/TTS/history side effect. |
| G3 | `_run_pipeline(..., generation)` and local `safe_put()` | Pipeline worker and MainWindow closure compare `generation <= _pipeline_cancel_generation` and lifecycle state. | Comparison is repeated ad hoc and does not protect callbacks created outside `safe_put`, such as direct TTS threads or report/farewell threads. | A single `is_current(generation_id)` check at every boundary. | **MIGRATE** | Stale check occurs before and after each blocking operation. |
| G4 | `_on_exit_program`, `_on_end_session`, `_end_session_with_relaxation` | UI handlers cancel the current pipeline and stop current TTS. | Cancellation can race with report/farewell and media threads; no shared generation record describes what was visible. | Delivery layer accepts cancellation commands from UI/lifecycle clients; SessionEngine remains lifecycle owner. | **DELEGATE** | End/exit cancels old delivery without changing Phase 4/5 command authority. |
| G5 | `app/engine.py` worker `threading.Thread` | SessionEngine owns lifecycle command serialization; its `_thread` is not a response-generation worker. | It must not be mistaken for a delivery generation owner. | Keep SessionEngine as lifecycle writer; delivery generation is a separate adapter-level concern. | **KEEP / EXCLUDE** | Phase 4 lifecycle boundary tests remain green; no generation state is moved into Engine. |

### 3.2 Provider stream and text assembly

| ID | Current entry/state | Current owner and mutation | Current risk/boundary | Phase 7 target owner | Disposition | Acceptance proof |
|---|---|---|---|---|---|---|
| L1 | `services/llm_service.py:LLMService.chat` | Ollama stream yields content chunks, accumulates `full_response`, and appends a normalized assistant message to `conversation_history` at generator completion. | Chunks have no generation ID. A late generator can still append history after a new user turn. Partial-error recovery appends raw/partial content independently of UI delivery. | Generation-scoped adapter wraps the existing iterator; provider transport stays unchanged, and history finalization moves to delivered-text ledger. | **MIGRATE BOUNDARY; KEEP PROVIDER** | Late chunks are dropped; no unplayed tail enters history. |
| L2 | `services/llm_factory.py:VLLMCompatibleLLMService.chat` | vLLM adapter appends user/assistant messages and yields `backend.stream_messages()` chunks. | Same unscoped stream/history side effect; provider callback cannot know stale status. | Delivery layer owns generation and final history commit; adapter remains vLLM-compatible. | **MIGRATE BOUNDARY; KEEP PROVIDER** | A100 `:8000` stream contract and history tests stay green. |
| L3 | `inference/vllm_client.py:stream_messages` | OpenAI-compatible client yields completion/chat delta content. | No cancellation token or generation metadata at this transport boundary. | Optional iterator close/cancel hook plus caller-side stale check; no provider redesign. | **KEEP + WRAP** | Existing vLLM client tests pass; closed/stale iterator has no side effect. |
| L4 | `services/pipeline.py:_stream_llm` | Joins all chunks into `generated_text`, calls `ResponseBuilder.build`, then emits one `stream_text` payload. | Despite the name, live UI/TTS cannot receive stable sentence units while the model is streaming; the full response is buffered first. | Generation assembler receives chunks, records `generated_text`, and emits `SentenceReady` as boundaries stabilize. | **MIGRATE** | Sentence events appear before provider completion; no token-level TTS. |
| L5 | `conversation/response_builder.py:BuiltResponse` | Stores `generated_text`, normalized `spoken_text`, `tts_text`, and compatibility `analysis_text`; uses legacy splitter defensively. | It normalizes only after whole output is available; it is not a delivery ledger. | Keep plain-text normalization before segmentation; no business/action inference. | **SIMPLIFY / KEEP** | Plain text remains unchanged; legacy tags cannot control a turn. |

### 3.3 UI event queue and visible delivery

| ID | Current entry/state | Current owner and mutation | Current risk/boundary | Phase 7 target owner | Disposition | Acceptance proof |
|---|---|---|---|---|---|---|
| U1 | `ui/main_window.py:processing_queue` and `process_queue()` | Background workers enqueue `(message_type, content)` tuples; the GUI timer drains them. | Tuples have no generation ID. A stale `stream_text`, `replace_greeting`, or `finish_streaming` can mutate the current bubble. | Queue typed generation events; GUI accepts only current, non-cancelled IDs. | **MIGRATE** | Stale UI events are ignored, and current event order is preserved. |
| U2 | `ui/chat_panel.py:start_ai_message`, `stream_text`, `finish_streaming` | `ChatPanel` creates a bubble, appends arbitrary chunks, and clears `_current_streaming_bubble`. | It has no sequence number, delivery ledger, duplicate rejection, or stale check. | ChatPanel consumes generation/sequence-scoped sentence/text events; visible append is the delivery commit point. | **MIGRATE** | Duplicate/out-of-order events do not duplicate visible text. |
| U3 | `ui/main_window.py:append_chat` | Queue handler may synchronously add a complete AI message and start/finish a bubble. | This is another unscoped path beside streaming events. | Route all assistant visible text through the same generation ledger, including fallbacks. | **SIMPLIFY** | Fallback is one delivered generation sentence, not an untracked side path. |
| U4 | `replace_greeting` / `_replace_greeting` | A background thread generates a replacement and the GUI replaces the latest AI bubble; `_play_tts_async` starts another daemon thread. | A late greeting can replace a newer message and play stale audio. | Greeting replacement carries the originating generation ID and is dropped when stale. | **MIGRATE** | A stale generated greeting cannot replace or speak. |
| U5 | `replace_last_system` / `_replace_last_system` | Post-relaxation generated text replaces the latest system bubble and starts direct TTS. | No generation or sequence contract; unrelated to normal pipeline cancellation. | Treat auxiliary generated text as a delivery generation using the same stale checks. | **MIGRATE** | Stale post-relaxation replacement has no UI/audio effect. |

### 3.4 TTS invocation, queueing, and playback

| ID | Current entry/state | Current owner and mutation | Current risk/boundary | Phase 7 target owner | Disposition | Acceptance proof |
|---|---|---|---|---|---|---|
| T1 | `adapters/protocols.py:TTSBackend.generate_and_play/stop_playing` | Protocol accepts one text string and exposes a global stop hook. | No generation/sequence identity; a late call cannot prove it belongs to the current turn. | Keep adapter surface; a single delivery worker validates generation before calling it. | **KEEP API / WRAP** | Existing adapter conformance remains green; stale calls never reach the adapter. |
| T2 | `services/pipeline.py:_play_tts` and `ThreadPoolExecutor.submit` | Pipeline submits one background task for the complete `result.tts_text` after LLM post-processing. | Whole-reply TTS, no sentence queue, and task future has no generation identity. | One shared generation-scoped sentence queue/worker owns ordering and cancellation. | **REPLACE LIVE PATH** | Two sentences produce two ordered calls; cancellation drains remaining calls. |
| T3 | `ui/main_window.py:_play_tts_async` | Every greeting/system notice creates a new daemon `threading.Thread` that calls `generate_and_play`. | Multiple direct callers race for the adapter lock; no stale callback or delivery record. | Route assistant-facing generated text through the same queue; non-conversational operator prompts may use an explicitly scoped auxiliary generation. | **MIGRATE** | No old direct thread can play after its ID is stale. |
| T4 | `services/tts_service_voxcpm.py:TTSService.generate_and_play` | VoxCPM2 preprocesses a whole string, uses `_play_lock`, internally streams audio chunks, and sets `is_playing`; `stop_playing()` sets false and calls `sd.stop()`. | Audio streaming exists, but text is not sentence-streamed and no caller generation is known. | Preserve VoxCPM2 model/audio streaming; let the Phase 7 worker pass one sentence and check stale state around the call. | **KEEP PROVIDER / ADAPT CALLER** | No model/provider changes; stop is best-effort and stale checks are authoritative. |
| T5 | `services/tts_service_cosyvoice.py:TTSService.generate_and_play` | CosyVoice3 synthesizes one whole text string into an internal playback queue/thread; `stop_playing()` clears `is_playing`. | Internal audio queue is not a cross-sentence delivery queue and has no generation metadata. | Preserve CosyVoice3; external worker controls sentence ordering and cancellation. | **KEEP PROVIDER / ADAPT CALLER** | Existing CosyVoice preflight tests stay green; no token TTS is added. |
| T6 | `_play_tts_then_auto_end` and session-end farewell TTS | MainWindow starts direct threads for notices/farewell; `_begin_report_flow` calls `generate_and_play(full_feedback)` after report/PDF. | Late farewell/notice can overlap a newer generation or survive a cancellation; report-first ordering must still hold. | Farewell gets its own generation ID and uses the same queue/stale rules after SessionEngine accepts end. | **MIGRATE BOUNDARY** | Report-first order remains; stale farewell cannot play after cancellation. |

### 3.5 History, persistence, and telemetry

| ID | Current entry/state | Current owner and mutation | Current risk/boundary | Phase 7 target owner | Disposition | Acceptance proof |
|---|---|---|---|---|---|---|
| H1 | `services/llm_service.py:conversation_history` | LLM service appends user input at request start and normalized full provider output at generator completion. | It can contain generated text that was never visible or audible after cancellation. | Delivery ledger finalizes assistant history from `delivered_text`; provider adapters stop being authoritative for the assistant history commit. | **MIGRATE** | Cancelled unshown tail is absent from next-turn history. |
| H2 | `services/llm_factory.py:conversation_history` | vLLM compatibility service mirrors the same append-at-completion behavior. | Same mismatch on stale/partial output. | Same generation-scoped delivered-history adapter; preserve bounded history trimming. | **MIGRATE** | Ollama/vLLM compatibility tests pass with delivered-only history. |
| H3 | `data/data_manager.py:save_user_message` / `save_assistant_message` | DataManager writes text files and metadata; pipeline calls `save_assistant_message(None, result.clean_spoken)` before TTS finishes. | The text is full normalized output, not explicitly tied to visible sentence delivery; no generation/seq metadata. | Pass the delivered text once after visible commit; audio progress may be stored separately without delaying text history. | **MIGRATE CALL SITE; KEEP STORAGE** | One assistant metadata record per generation; no stale write. |
| H4 | `services/pipeline.py:spoken_text`, `clean_spoken`, `tts_text` | Pipeline derives display/TTS values after complete ResponseBuilder output. | Names are text projections, not a generated/delivered ledger; full result is saved before asynchronous playback. | Keep normalized projections as sentence payloads; add explicit generated/delivered fields at delivery layer. | **SIMPLIFY** | History receives only delivered projection; TTS receives normalized sentence. |
| H5 | `services/report_service.py` and report tools' `conversation_history` | Reports consume the LLM history at end flow; farewell is generated/streamed separately. | Report reads must not become a second delivery authority or reinsert cancelled tails. | Reports read finalized delivered history; historical cleanup remains compatibility-only. | **KEEP AS READER / MIGRATE INPUT** | Phase 4/5 report lifecycle tests remain green. |
| H6 | `generated_text` occurrences | `BuiltResponse.generated_text` and local `_stream_llm` value exist only as diagnostic/intermediate data. | No end-to-end generation record or bounded telemetry contract exists. | Retain bounded raw generated text per generation for diagnostics only. | **ADD DELIVERY FIELD** | Test proves generated tail is not history. |

### 3.6 Lifecycle and auxiliary paths

| ID | Current entry/state | Current owner and mutation | Current risk/boundary | Phase 7 target owner | Disposition | Acceptance proof |
|---|---|---|---|---|---|---|
| X1 | `app/engine.py:SessionEngine` lifecycle thread/events | Engine remains the sole lifecycle writer and emits events; it tracks playback kind but not sentence delivery. | Moving generation state into Engine would reopen Phase 4 and mix delivery with lifecycle policy. | Keep Engine unchanged except for typed delivery notifications if strictly needed; delivery worker remains separate. | **EXCLUDE FROM MIGRATION** | No new Phase 4/5 production diff; lifecycle boundary passes. |
| X2 | `_on_continue_chosen`, `_play_post_relaxation_greeting`, media completion | MainWindow creates system messages and direct TTS after SessionEngine commands/events. | Auxiliary text/audio is not linked to a turn and can race with a new user input. | Allocate scoped auxiliary generation IDs; do not change relaxation/game/end eligibility. | **MIGRATE DELIVERY ONLY** | Phase 5 policy tests still define approval; only delivery becomes cancellable. |
| X3 | `generate_farewell_and_reports` | Background `threading.Thread` streams report feedback into unscoped queue events, saves report, then plays full farewell. | Late stream/UI/TTS callbacks can survive shutdown or cancellation; `full_feedback` is raw accumulation. | Use generation-scoped events and delivered history; keep report-first and SessionEngine-approved end semantics. | **MIGRATE DELIVERY ONLY** | Farewell cancellation and shutdown tests; report order unchanged. |
| X4 | STT recording threads and VAD stop | MainWindow/STT use daemon threads; VAD invokes `_on_record_stopped`. | Recording cancellation is input capture, not assistant delivery; it must not be confused with TTS cancellation. | Keep STT/VAD architecture; starting a new assistant generation cancels only prior delivery. | **KEEP / SEPARATE** | Existing STT/VAD tests remain green. |

## 4. Required Phase 7 objects and ownership after migration

The following target objects are design concepts, not current files:

| Target object | Owns | Must not own |
|---|---|---|
| `GenerationController` (or equivalent delivery-layer object) | Current ID, cancellation event, stale checks, generation lifecycle. | TurnPolicy, ScaleRuntime, SessionEngine, RAG, or business rules. |
| `TextAssembler` | Bounded generated-text accumulation and `TextChunk` forwarding. | History commit, TTS playback, or action inference. |
| `SentenceSegmenter` | Deterministic punctuation/max-bound/flush behavior and sequence numbers. | Natural-language intent or scoring. |
| `SentenceDeliveryQueue` / worker | Ordered `SentenceReady` acceptance, TTS calls, queue drain, audio progress. | Session transitions, scale changes, or new policy decisions. |
| `DeliveryLedger` | Visible sentence commits, delivered prefix, one-time history finalization, audio progress metadata. | Raw model authority or lifecycle commands. |

These may be combined into fewer files if the single-writer boundaries remain
explicit. They must not be added to `SessionEngine` or `TurnPolicy` merely for
convenience.

## 5. Current absence checklist

The following required Phase 7 capabilities were searched for and are not
present as a coherent live contract at this baseline:

| Required capability | Baseline finding |
|---|---|
| Generation ID on LLM chunks | **ABSENT**; only MainWindow's bare integer reaches `_run_pipeline`. |
| `SentenceReady(generation_id, seq, text)` | **ABSENT**. |
| One external sentence TTS queue/worker | **ABSENT**; Pipeline and MainWindow call TTS independently. |
| Generation cancellation event/token | **ABSENT**; numeric cutoff plus adapter stop hook only. |
| Stale checks after synthesis/playback callbacks | **ABSENT**. |
| UI delivery ledger | **ABSENT**; ChatPanel stores bubble text only. |
| `delivered_text` history contract | **ABSENT**; full normalized output is saved/appended on pipeline/provider completion. |
| Generated-vs-delivered telemetry separation | **ABSENT** end to end; `BuiltResponse.generated_text` is local/diagnostic. |
| Sentence segmentation for `。！？……` | **ABSENT**; complete response is passed to TTS. |
| Token-by-token TTS | **ABSENT**, and it must remain absent. |

## 6. Compatibility and boundary classification

| Existing surface | Phase 7 classification | Reason |
|---|---|---|
| vLLM/Ollama streaming iterators | **KEEP / WRAP** | Provider architecture and endpoints are frozen. |
| VoxCPM2/CosyVoice internal audio streaming | **KEEP** | It is model/audio chunking, not the external sentence queue. |
| `generate_and_play(text)` / `stop_playing()` | **KEEP adapter API** | A delivery worker supplies sentence text and owns stale checks. |
| `core/tags.py` cleanup and historical delimiter parsing | **COMPATIBILITY ONLY** | It may clean old content but cannot control a live turn. |
| `ResponseBuilder.analysis_text` | **COMPATIBILITY/REPORTING ONLY** | It cannot affect delivery, action, or history authority. |
| `DataManager` text/audio persistence | **KEEP STORAGE; CHANGE INPUT CONTRACT** | Persist delivered text once; track audio progress separately. |
| `SessionEngine` lifecycle events | **KEEP** | No Phase 4/5 authority migration. |
| STT/VAD threads | **KEEP / SEPARATE** | Input capture cancellation is distinct from assistant delivery cancellation. |

## 7. Inventory acceptance gate

Before the Phase 7 production commit, the implementation must show:

1. every live provider chunk, sentence, UI callback, TTS request, playback
   callback, and history finalizer carries a generation ID;
2. exactly one current-generation/cancellation owner exists;
3. a stale ID has no side effect at all listed boundaries;
4. visible UI text, not raw generated text or audio completion, defines
   `delivered_text`;
5. one ordered sentence queue is the only normal assistant TTS entry point;
6. farewell, greeting, and post-relaxation generated text use the same stale
   contract without changing SessionEngine/Phase 5 policy;
7. current TTS providers, vLLM/Ollama endpoints, STT, VAD, and A100 launch
   scripts are unchanged; and
8. the full suite remains green and the implementation record states the
   measured sentence segmenter configuration and actual local smoke status.

No Phase 7 production code, `SentenceReady` class, generation-aware queue,
delivery ledger, or TTS cancellation implementation exists at this inventory
baseline. The next action after this docs-only freeze is the separately
reviewed implementation commit, not a Phase 8 redesign.
