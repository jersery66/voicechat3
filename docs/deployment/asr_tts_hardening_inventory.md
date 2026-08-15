# Deployment hardening: ASR/TTS code-fact inventory

> Read-only baseline for the next deployment-hardening work. This document
> records the current code and call paths; it does not change production
> behavior and it is not a new architecture phase.

## 1. Baseline and scope

- Date: 2026-08-15.
- Branch: `codex/a100-vllm-safety`.
- Baseline HEAD: `17984a82accfc8b66bde0b6fd1eff20fc8d768f0`
  (`docs: record phase 8 remote verification`).
- Working tree was clean before this inventory.
- Baseline command:
  `E:\Anaconda\python.exe -m pytest tests -q` with the repository's
  `voice_chat` site-packages on `PYTHONPATH`.
- Baseline result: **517 passed, 1 skipped, 0 failed** in 103.01 seconds.
- The single skip is the existing PySide6/QtWidgets DLL-load limitation on
  this Windows development environment. The process also prints the known
  `0xc0000139` Qt diagnostic after the successful pytest summary.
- Real microphone, FunASR/FSMN-VAD, VoxCPM2/CosyVoice, sound-card, A100, and
  vLLM smoke were **NOT RUN / environment unavailable** during this freeze.

The inventory covers the live production path, not only the future-facing
`voice/` protocols. It includes:

```text
services/stt_service.py
services/tts_service.py
services/tts_service_voxcpm.py
services/tts_service_cosyvoice.py
conversation/delivery.py
services/pipeline.py
ui/main_window.py
config.py
adapters/protocols.py
tests/test_stt_cleanup.py
tests/test_tts_preflight.py
tests/test_phase7_delivery_boundary.py
tests/test_voice_protocols.py
tests/test_voice_runtime_contracts.py
```

## 2. Current live call chain

### 2.1 Input / ASR

```text
RecordButton.started
  -> ui/main_window.py:_on_record_started
  -> daemon thread: STTService.start_recording()
  -> sounddevice.InputStream(callback=audio_callback)
  -> audio_queue.Queue[np.ndarray]
  -> collector thread: recorded_audio
  -> VAD flag or RecordButton.stopped
  -> ui/main_window.py:_on_record_stopped
  -> daemon thread: MainWindow._run_pipeline(None, generation)
  -> STTService.stop_recording()
  -> PipelineConfig(audio_data=...)
  -> ConversationCoordinator / ConversationPipeline
  -> STTService.transcribe(audio)
```

Evidence:

- `ui/widgets.py:111-149` emits `started`/`stopped` and is the manual-stop
  control.
- `ui/main_window.py:332-373` updates the visible recording state before the
  background `start_recording()` call returns.
- `ui/main_window.py:375-385` starts the voice pipeline, which later calls
  `stop_recording()`.
- `ui/main_window.py:656-661` polls `is_vad_triggered()` from the Qt timer and
  invokes the same stop path.
- `ui/main_window.py:492-523` puts returned audio into `PipelineConfig`.

### 2.2 Output / TTS

```text
MainWindow.load_models()
  -> from services.tts_service import TTSService
  -> services.tts_service_voxcpm.TTSService
  -> VoxCPM2.load_model()

ConversationPipeline._emit_generation_sentence()
  -> SentenceDeliveryQueue.enqueue(SentenceReady)
  -> one worker: SentenceDeliveryQueue._run()
  -> TTSService.generate_and_play(sentence.tts_text)
  -> VoxCPM2 internal audio streaming / sounddevice.OutputStream
  -> stop_playing() on generation cancellation
```

Evidence:

- `services/tts_service.py:1-4` exports the VoxCPM2 implementation. Its
  comment still says CosyVoice, so the comment is stale but the import is
  unambiguous.
- `ui/main_window.py:880-945` constructs STT/TTS directly; the adapter factory
  is not the live voice-service selector.
- `services/pipeline.py:388-410` constructs the shared
  `SentenceDeliveryQueue`.
- `services/pipeline.py:1841-1862` normalizes a stable sentence, enqueues it,
  and emits the UI event.
- `conversation/delivery.py:466-590` owns one TTS worker and checks the
  generation before and after the blocking adapter call.
- `services/tts_service_voxcpm.py:205-350` performs model/audio streaming;
  `:390-392` is the current stop hook.

CosyVoice is not the selected production backend. Its implementation remains
under `services/tts_service_cosyvoice.py` for experiment/compatibility use and
is not a production backend-switching contract.

## 3. ASR facts and ownership

| ID | Current location | Current fact / owner | Risk or missing contract | Hardening disposition |
|---|---|---|---|---|
| A1 | `services/stt_service.py:57-75` | `STTService` owns `is_recording`, an unbounded `queue.Queue`, `recorded_audio`, stream handle, and energy-VAD counters. | Capture lifecycle is spread across a callback, collector thread, Qt polling, and MainWindow flags. | **CONSOLIDATE capture lifecycle**, without changing pipeline/business authority. |
| A2 | `:166-196` | The callback enqueues a copy, updates RMS counters, and sets `is_recording=False` when silence duration is reached. | The callback can stop the collector condition immediately after queueing the final frame. | **FIX with stop-request + sentinel**, never close the stream in the audio callback. |
| A3 | `:258-270` | Collector loops `while self.is_recording`, drains with a timeout, then closes the stream. | It can exit before draining frames already queued after the final callback. | **REPLACE with sentinel/drain semantics**; preserve manual stop. |
| A4 | `:296-309` | `stop_recording()` sets the flag, closes the stream, joins for one second, and concatenates what the collector stored. | The join is not proof that the queue was drained; tail samples can be missing. | **Add deterministic lifecycle tests** for final-chunk preservation and repeated stop. |
| A5 | `:197-256` | Device selection is heuristic: named microphone, virtual input, default input, then any input; `InputStream` is 16 kHz mono float32 with blocksize 1024. | Device discovery and stream-open failures are only visible in logs. | **Keep selection order**, expose a typed start failure to MainWindow. |
| A6 | `:273-279` | `start_recording()` catches every startup exception, sets `is_recording=False`, logs, and returns `None`. | MainWindow already shows “正在录音...” and has no failure event/result. | **PROPAGATE failure** through the existing UI queue; no silent “recording” state. |
| A7 | `config.py:525-533` and `stt_service.py:181-195` | Endpointing is a fixed RMS threshold (`0.01`), 1.5 s silence, and 0.5 s speech minimum. | Threshold is microphone/environment dependent; no FSMN-VAD is wired. | **INTEGRATE verified FSMN-VAD adapter**; do not invent unsupported FunASR arguments. |
| A8 | `voice/vad.py:8-10`, `voice/turn_detector.py:6-28` | Protocols and a deterministic silence detector exist, but no live `STTService` call site uses them. | Future-facing contracts can be mistaken for production endpointing. | **Either wire through a tested adapter or leave explicitly non-live**; do not duplicate VAD state. |
| A9 | `:76-159` | Loading imports a model-local `FunASRNano`, temporarily monkey-patches `torch.nn.Module.to`, and normalizes mixed dtypes to float32. | This is a checkpoint-specific private wrapper, not the documented `funasr.AutoModel` path. | **VERIFY/pin installed API before migration**; keep current loader working until replacement is tested. |
| A10 | `:318-393` | Transcription writes a temporary WAV, calls `model.inference`, retries if text is not mostly Chinese, and returns `""` on all exceptions. | Inference failures are indistinguishable from a valid empty utterance. | **Separate empty audio, no-speech, and inference failure outcomes** at the service/UI boundary. |
| A11 | `:429-460` | `_correct_common_errors()` performs unconditional substring replacements, including drug terms. | It can alter negation, frequency, duration, numbers, or scale-critical meaning if a false match occurs. | **Retain only narrow, audited corrections**; ambiguous scale semantics require confirmation. |
| A12 | Repository search | No hotword configuration or `fsmn-vad` production call site exists; only the protocol names occur under `voice/`. | Current quality relies on post-hoc replacement rather than model vocabulary support. | **Add reviewed domain hotwords through the verified ASR API**, with an offline test fixture. |
| A13 | `tests/test_stt_cleanup.py` | Tests cover cleanup/resource release and mixed-dtype model loading. | No tests cover queue drain, startup failure, manual stop, VAD endpoint, or transcription error distinction. | **Add ASR lifecycle regression suite before code changes.** |
| A14 | `ui/main_window.py:332-385`, `services/stt_service.py:166-304` | Start runs on a daemon thread while stop can be requested immediately from the UI. | Stop may run before the stream exists; the start thread can then open a stream after the UI has already stopped. | **Serialize start/stop requests** and make late start fail harmlessly. |
| A15 | `ui/main_window.py:656-661`, `services/stt_service.py:181-196,281-304` | VAD callback/collector and the 50 ms UI poll can all reach stream shutdown. | Double-stop/close is currently caught and logged rather than represented as one lifecycle transition. | **Make stop transition single-shot and observable.** |
| A16 | `services/stt_service.py:168-173,296-304` | Start replaces the queue/list while an old collector may still be alive after the one-second join timeout. | An old collector can consume or append frames belonging to a new recording. | **Use per-recording state/sentinel ownership** and test rapid restart isolation. |

### ASR authority boundary

The ASR hardening may change capture, endpointing, error propagation, and
text normalization. It must not change `TurnPolicy`, `TurnDecision`,
`ScaleRuntime`, `SessionEngine`, RAG gating, or the interpretation of a
structured scale answer. The ASR output remains one final utterance passed into
the existing pipeline.

## 4. TTS facts and ownership

| ID | Current location | Current fact / owner | Risk or missing contract | Hardening disposition |
|---|---|---|---|---|
| T1 | `services/tts_service.py:1-4`, `ui/main_window.py:883-925` | The live selector imports VoxCPM2 directly; MainWindow constructs it. | The selector comment claims CosyVoice and no real backend switch exists. | **Correct documentation/comments only when implementation starts**; keep VoxCPM2 production-only. |
| T2 | `services/tts_service_voxcpm.py:106-123` | Loader constructs `VoxCPM(...)` using the current legacy constructor. | Public loader/API version is not pinned in the repository contract. | **Pin and record the tested VoxCPM version** before any public-API migration. |
| T3 | `:131-145` | Prompt cache calls `self.model.tts_model.build_prompt_cache(...)`. | Cache behavior is provider-specific and must remain version-tested. | **Keep while pinned**; migrate separately after hardware validation. |
| T4 | `:205-221` | `generate_and_play()` serializes with `_play_lock`; empty normalized text returns without a status value. | DeliveryQueue cannot distinguish empty/cancelled/completed from a normal return. | **Define explicit completed/cancelled/failed result semantics.** |
| T5 | `:290-350` | Prompt-cache generation uses private `_generate_with_prompt_cache`; fallback uses public `generate_streaming`; exceptions are logged and swallowed. | A generation failure can look like successful playback to the caller; private API can break on upgrade. | **Propagate failure**, retain private API only under a pinned version, add provider seam tests. |
| T6 | `:239-285` | VoxCPM starts playback after a fixed 0.8 s sample prebuffer and keeps a shared `is_playing` flag. | First-audio latency and underrun behavior are hardware-dependent; no measured baseline exists. | **Measure 0.8/0.5/0.3 s on A100 before tuning**; do not guess in code freeze. |
| T7 | `:390-392` | `stop_playing()` sets `is_playing=False` and calls `sd.stop()`. | It is best-effort and not represented in a completed/cancelled/failed result. | **Make stop idempotent and test stale-generation interruption**, retaining the adapter hook. |
| T8 | `services/tts_service_cosyvoice.py:243-321` | CosyVoice catches generation errors, uses a private playback queue/worker, and only toggles `is_playing` on stop (`:353-355`). | `stop_playing()` cannot signal the local `stop_event`; buffered chunks are flushed at `:215-220`, so cancelled audio can continue. | **Keep experimental/legacy only**; no production switch until cancellation is independently fixed. |
| T9 | `adapters/protocols.py:80-86` | `TTSBackend.generate_and_play(text) -> None` and `stop_playing() -> None`. | Protocol has no explicit status/error contract. | **Extend only the TTS adapter contract**, not any business authority. |
| T10 | `conversation/delivery.py:525-590` | One worker checks current generation and sets `AudioFinished.ok=True` after any normal return. | Provider-swallowed exceptions and empty/cancelled returns are recorded as success. | **Map explicit provider status to completed/cancelled/failed**; preserve one worker and stale checks. |
| T11 | `services/tts_service_voxcpm.py:352-388`, `services/tts_service_cosyvoice.py:323-351` | Offline `generate()` APIs return empty arrays on errors. | This is acceptable for report/audio helpers only if callers distinguish empty output from a valid zero-length result. | **Add narrow error/result tests**, avoid changing report semantics in this hardening slice. |
| T12 | `tests/test_tts_preflight.py`, `tests/test_phase7_delivery_boundary.py` | Tests cover 6 GB GPU preflight, cleanup, generation IDs, sentence ordering, and stale queue callbacks. | No provider failure propagation, cancellation status, stop latency, underrun, or real audio tests exist. | **Add deterministic TTS contract suite plus a separate real-device log.** |
| T13 | `services/tts_service_voxcpm.py:166-178`, `services/tts_service_cosyvoice.py:134-147`, `ui/main_window.py:961-965` | Warmup can return `True` after empty audio and MainWindow does not use the result to disable TTS. | A failed/empty warmup can leave a broken service in the live path. | **Make warmup availability explicit** without changing text-chat fallback. |
| T14 | `services/tts_service_voxcpm.py:232-239,307-339` | Vox uses a fixed 120-second ring buffer with no producer backpressure/capacity check. | A fast producer can overwrite unread audio; an oversized chunk can violate the wrap logic. | **Add bounded-buffer tests and safe overflow handling** before latency tuning. |
| T15 | `services/tts_service_cosyvoice.py:279-321,403-414` | Cosy starts a playback worker, can return early on missing prompt, and cleanup does not call `unload_model()`. | A worker can outlive its stream/model; buffered chunks may continue after cancellation. | **Contain CosyVoice as non-production** and add cancellation/resource tests only. |
| T16 | `conversation/delivery.py:545-551` | Queue shutdown joins for two seconds then clears the thread reference even if the daemon is still blocked. | A later `start()` can create a second worker while the old one remains alive. | **Make shutdown ownership observable and single-worker-safe.** |
| T17 | `services/pipeline.py:1840-1861` | Generation-scoped `_emit_generation_sentence()` always enqueues TTS; `PipelineConfig.use_tts` only gates the compatibility path. | A caller requesting no TTS can still invoke the provider. | **Honor the existing `use_tts` flag**; do not add a new policy. |
| T18 | `services/pipeline.py:1856-1862`, `ui/main_window.py:695-707` | A sentence is queued for audio before its UI event is committed; UI commits the ledger before ChatPanel append. | Cancellation in the gap can make audio/history and visible UI disagree. | **Test and close the delivery commit barrier** without changing delivered-text semantics. |
| T19 | `ui/main_window.py:337-345` | New recording cancels only when `_pipeline_busy` is true. | Queued TTS from a completed pipeline may continue into the next user turn. | **Cancel pending delivery on every new recording**, while preserving generation ownership. |
| T20 | `ui/main_window.py:2093-2100,2128-2141` | End flow stops current audio before allocating the farewell generation later. | Old queued sentences can run during the report/farewell transition. | **Cancel old generation before report/farewell work**; retain report-first ordering. |
| T21 | `conversation/delivery.py:259-272`, `services/pipeline.py:1928-1939` | `max_wait_ms=800` is checked only when another provider chunk arrives. | A provider stall can exceed the configured first-audio bound. | **Add a bounded flush mechanism/test**; do not add token-level TTS. |
| T22 | `conversation/delivery.py:479`, `:103-105,368-373` | Sentence queue and generation records retain unbounded work/text. | Long sessions can accumulate memory and queued stale items. | **Bound queue/record retention** with stale-drop rules and telemetry. |
| T23 | `services/pipeline.py:404-410` | The live queue is constructed without `on_event`; audio progress events are discarded. | Real cancellation/latency evidence cannot be logged from the delivery layer. | **Expose bounded audio status telemetry** without making it policy. |

### TTS authority boundary

The TTS hardening may change provider error/stop semantics and delivery
telemetry. It must not change sentence ordering, `GenerationController`
ownership, `TurnPolicy`, `ScaleRuntime`, `SessionEngine`, or Phase 7 history
rules. `SentenceDeliveryQueue` remains the only normal assistant sentence
entry point; CosyVoice remains out of the production selector.

## 5. Cross-cutting gaps and preserved contracts

| Area | Current fact | Hardening rule |
|---|---|---|
| Adapter construction | `ui/main_window.py:880-1047` directly constructs STT/TTS/Data/Report while `adapters.factory.py` is not the voice selector. | Do not widen this task into a factory rewrite; record it separately if needed. |
| Generation | `conversation/delivery.py` is already the Phase 7 owner; `MainWindow` and `Pipeline` pass generation IDs on the live path. | Do not create a second generation counter or move it into SessionEngine. |
| History | `DeliveryLedger.finalize_history()` owns delivered-only assistant persistence. | TTS status cannot rewrite `delivered_text` or add a phantom assistant turn. |
| RAG/policy | `TurnDecision.needs_rag`, TurnPolicy, ScaleRuntime, and SessionEngine are frozen. | No ASR/TTS change may add intent/action/end/scale/RAG decisions. |
| Deployment | Existing vLLM 72B `:8000`, Router `:8001`, FunASR, VAD, VoxCPM2, and A100 scripts are frozen. | Provider wrapper hardening must preserve endpoints, model placement, and launch contracts. |
| Real smoke | No real A100/audio-device validation was run in this inventory. | Report `NOT RUN / environment unavailable`; do not infer from fakes. |

## 6. Inventory acceptance gate

This inventory is complete for the first hardening slice when the
implementation plan can answer all of these without touching business policy:

1. How does a recording stop request guarantee that every queued frame before
   the sentinel is included exactly once?
2. How does MainWindow receive microphone-open and capture failures?
3. Which verified FunASR/FSMN-VAD API and version provide endpointing and
   hotwords?
4. How are empty, completed, cancelled, and failed TTS calls distinguished?
5. How does cancellation stop current audio, drain pending work, and prevent
   stale callbacks without changing delivered history?
6. Which tests prove these contracts without requiring a microphone, CUDA,
   model download, or speaker?
7. Which measurements remain **NOT RUN** until the A100/audio environment is
   available?

The next artifact is the companion frozen implementation/test specification.
No production code is authorized by this inventory alone.
