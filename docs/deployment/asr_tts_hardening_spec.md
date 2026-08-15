# Deployment hardening: ASR/TTS implementation and test freeze

> This is a deployment-hardening specification after the eight architecture
> phases. It is not Phase 9, does not reopen the authority chain, and does not
> implement production changes.

## 1. Freeze status and baseline

- Status: **design, ownership inventory, and test plan frozen; production
  implementation not started**.
- Date: 2026-08-15.
- Branch: `codex/a100-vllm-safety`.
- Baseline HEAD: `17984a82accfc8b66bde0b6fd1eff20fc8d768f0`.
- Baseline full regression: **517 passed, 1 skipped, 0 failed**.
- Known skip: PySide6/QtWidgets cannot load a Windows Qt DLL in the current
  development environment; the existing post-summary `0xc0000139` diagnostic
  is recorded, not treated as a product pass.
- Real microphone/audio/model smoke: **NOT RUN / environment unavailable**.
- Companion fact inventory: `docs/deployment/asr_tts_hardening_inventory.md`.
- No production Python, configuration, model endpoint, prompt, RAG,
  TurnPolicy, ScaleRuntime, SessionEngine, or report schema is changed by this
  freeze.

## 2. Frozen scope and authority boundaries

### In scope

```text
ASR capture lifecycle, queue draining, startup/error feedback,
endpoint/VAD adapter integration, reviewed hotwords, narrow ASR cleanup,
TTS completion status, provider failure propagation, cancellation/stop
semantics, deterministic wrapper tests, and real-device acceptance logging.
```

### Explicitly out of scope

- No new architecture Phase or new business-policy layer.
- No changes to `RouterProposal`, `TurnPolicy`, `TurnDecision`,
  `ScaleRuntime`, `ScaleAnswerInterpreter`, or `SessionEngine` semantics.
- No change to `TurnDecision.needs_rag`, production RAG allowlist, prompts,
  history protocol, report policy, or Phase 7 sentence segmentation.
- No production CosyVoice backend switch.
- No Qwen3.6/model-profile migration in this slice.
- No partial-ASR UI, token-level TTS, new generation contract, or new
  sentence queue. Those are already frozen by Phase 7.
- No broad adapter-factory rewrite; this slice hardens the existing live
  provider seams and their lifecycle/error contracts only.
- No automatic PHQ-9 Q9 crisis policy. Operational handling remains a
  separately governed deployment procedure and must not be reintroduced into
  the runtime through ASR/TTS work.

### Required authority chain after hardening

```text
RouterProposal -> TurnPolicy -> exactly one TurnDecision
ScaleAnswerInterpreter -> ScaleRuntime
SessionEngine -> session lifecycle
TurnDecision.needs_rag -> RAG gate
GenerationController/SentenceDeliveryQueue -> delivery and TTS only
MainWindow -> UI/device commands and rendering
```

ASR returns one final utterance. TTS reports playback status. Neither may
decide action, score a scale, advance a question, end a session, recommend
relaxation/game, or enable RAG.

## 3. Frozen ASR implementation design

### 3.1 Lossless recording shutdown

The recording service keeps the public adapter surface:

```python
start_recording()        # raises/returns a typed startup failure
stop_recording() -> np.ndarray
is_vad_triggered() -> bool
transcribe(audio) -> str
```

The internal lifecycle is frozen as:

```text
start request
  -> clear per-recording state and create a fresh queue
  -> open InputStream
  -> callback copies frames while capture is open
  -> endpoint/manual stop sets stop_requested
  -> callback stops accepting new frames
  -> producer/collector inserts exactly one sentinel
  -> collector drains through sentinel
  -> stream closes outside the audio callback
  -> concatenate drained frames once
```

Rules:

1. `stop_recording()` is idempotent and remains the manual-stop path.
2. The last callback frame is included before the sentinel; a stop flag alone
   is not a drain protocol.
3. Stream-close errors are reported as capture failures but do not deadlock
   the collector.
4. A second recording cannot consume frames from the previous queue.
5. No callback closes the active `sounddevice.InputStream` directly.

### 3.2 Startup and capture failures

`start_recording()` must expose failure to the caller. The implementation may
use a typed result or a dedicated exception, but the observable contract is
fixed:

```text
success -> MainWindow leaves the recording control active
failure -> MainWindow resets the control and posts a participant-safe status
           while logging the technical error
```

The background worker must not silently turn a microphone-open failure into a
normal empty transcript. `transcribe()` must preserve the distinction between
empty input/no speech and an inference failure for logging and UI fallback;
the existing coordinator/pipeline behavior for an empty utterance remains
unchanged.

### 3.3 Endpointing and FunASR API gate

The primary endpoint detector becomes the installed, verified FSMN-VAD path.
The implementation must first record the exact FunASR package/model API and
version used on the deployment machine. It may not invent keyword arguments
such as `vad_model` or `hotwords` without verifying that the installed wrapper
accepts them.

Frozen endpoint behavior:

- speech start and post-speech silence are determined by the VAD adapter;
- manual stop always wins and uses the same lossless drain;
- no partial transcript is rendered;
- final ASR runs once on the completed utterance;
- RMS may remain as a diagnostic/fallback only if explicitly labeled and
  tested, not as a second competing owner of endpoint state.

Frozen domain vocabulary is a reviewed configuration input, not an expanding
replace-all dictionary. Candidate terms for the first deployment fixture are:

```text
戒毒、强制隔离戒毒、冰毒、海洛因、甲基苯丙胺、焦虑、抑郁、心慌、
失眠、戒断、渴求、复吸
```

ASR post-processing may correct a small, audited set of obvious errors, but
must not silently change negation, frequency, duration, quantities, drug
names, or scale answers. Ambiguity in those fields goes back to the ordinary
conversation/scale clarification path.

## 4. Frozen TTS implementation design

### 4.1 Production backend and provider preservation

```text
Production: VoxCPM2 only
Experimental/legacy: CosyVoice3, not selected by production factory
```

Keep the Phase 7 path:

```text
SentenceSegmenter -> SentenceReady -> SentenceDeliveryQueue
                   -> one TTS worker -> provider audio streaming
```

Do not replace it with token TTS, a second queue, or direct parallel provider
threads. Preserve the existing VoxCPM2 sample rate, voice prompt, A100 model
placement, and `stop_playing()` adapter hook.

### 4.2 Explicit playback result

The adapter contract must distinguish three terminal outcomes:

```text
COMPLETED  - synthesis and playback reached normal end
CANCELLED  - stop was requested or generation became stale
FAILED     - provider/audio error prevented normal completion
```

The implementation may represent this as a small enum/result object or a
documented exception mapping, but `SentenceDeliveryQueue` must not infer
success merely because `generate_and_play()` returned. In particular:

- VoxCPM2 and CosyVoice must not swallow provider exceptions on the live path.
- Empty normalized text is not a successful audio playback.
- A stale generation after a blocking call must be recorded as cancelled, not
  completed.
- A TTS failure cannot erase visible `delivered_text` or create a duplicate
  history entry.
- `stop_playing()` is idempotent and best-effort; generation staleness remains
  authoritative.
- Empty or failed warmup must explicitly mark TTS unavailable; it cannot leave
  a silently broken service enabled.

### 4.3 VoxCPM2 compatibility gate

The current prompt-cache path calls the private
`tts_model._generate_with_prompt_cache` API. Before migrating it, the
implementation must pin the currently tested VoxCPM2 version and record:

```text
package/version
model path and config marker
prompt-cache path used or disabled
public fallback path
first-audio latency and stop latency
```

Public API migration is a separate, reviewable hardening change. It must not
be combined with ASR endpoint migration or a model swap.

The current Vox ring buffer and queue lifecycle are also part of hardening:
producer overflow must not overwrite unread samples, and a timed-out queue
shutdown must not permit a second worker to start while the first remains
alive. The generation path must honor the existing `PipelineConfig.use_tts`
flag rather than adding a new policy switch.

### 4.4 CosyVoice containment

CosyVoice remains compatibility/experiment code until it has its own tested
stop-event, queue-drain, and no-flush-after-cancel contract. The current
implementation's local `stop_event` and buffered-chunk flush cannot be
treated as equivalent to the Phase 7 cancellation contract. No production
backend toggle is added in this slice.

## 5. Frozen implementation sequence and commits

Production work, when authorized after this freeze, is split into small
rollbackable commits:

1. `fix: make stt recording shutdown lossless`
2. `fix: propagate microphone startup failures`
3. `refactor: integrate fsmn vad for utterance endpointing`
4. `test: add stt recording lifecycle regressions`
5. `fix: make tts completion status explicit`
6. `fix: harden voxcpm cancellation semantics`
7. `test: add tts cancellation and failure regressions`

Prompt changes are separate:

8. `refactor: refine participant-facing conversation prompts`

Model migration is last and isolated:

9. `feat: add qwen36 dialogue deployment candidate`
10. `test: add dialogue model profile compatibility coverage`

No commit may combine ASR/TTS hardening with TurnPolicy, ScaleRuntime,
SessionEngine, prompt authority, RAG, or dialogue-model migration.

## 6. Frozen test plan

### 6.1 Test-only seams

Tests may fake `sounddevice.InputStream`, device enumeration, FunASR model
inference, `sd.OutputStream`, VoxCPM generators, and PyAudio. They must not
claim that a fake proves real microphone, CUDA, speaker, or model behavior.

### 6.2 ASR deterministic tests

| ID | Test assertion |
|---|---|
| ASR-01 | A successful start resets queue/state and exposes the active recording state. |
| ASR-02 | Device enumeration/open failure reaches MainWindow as a participant-safe failure and resets the control. |
| ASR-03 | A callback frame queued immediately before auto-stop is present in the final concatenated audio. |
| ASR-04 | Manual stop drains through the sentinel, closes the stream outside the callback, and returns frames in order. |
| ASR-05 | Repeated stop/cleanup is idempotent and does not double-close or deadlock. |
| ASR-06 | Auto endpointing fires once after the configured post-speech condition; silence before speech does not finalize. |
| ASR-07 | Verified FSMN-VAD adapter receives fixed-rate frames and remains the single endpoint owner. |
| ASR-08 | Final ASR is invoked once per completed utterance; no partial-ASR UI event is emitted. |
| ASR-09 | Empty/no-speech, model failure, and malformed model output have distinct deterministic outcomes/log markers. |
| ASR-10 | Reviewed hotwords are passed through the verified ASR seam; no broad replace-all correction is used. |
| ASR-11 | Negation, frequency, duration, quantity, and scale-critical text are never silently rewritten by cleanup. |
| ASR-12 | Existing cleanup/model-load tests remain green. |
| ASR-13 | An immediate stop while start is still opening the device cannot reopen a stream or leave STT active after the UI stopped. |
| ASR-14 | VAD, manual stop, and cleanup converge on one idempotent stream-close transition. |
| ASR-15 | Rapid stop/start uses per-recording ownership; an old collector cannot write into the new recording. |

### 6.3 TTS deterministic tests

| ID | Test assertion |
|---|---|
| TTS-01 | Normal sentence playback returns/emits `COMPLETED` exactly once. |
| TTS-02 | Provider exception becomes `FAILED`; it is not converted into success by an internal catch. |
| TTS-03 | Empty normalized text is not reported as completed audio. |
| TTS-04 | Cancellation during generation returns/emits `CANCELLED`, calls `stop_playing()` best-effort, and drains pending work. |
| TTS-05 | Cancellation after the provider returns but before side effects suppresses stale completion callbacks. |
| TTS-06 | One worker preserves sentence order and never invokes two providers concurrently. |
| TTS-07 | TTS failure preserves visible delivery/history and does not duplicate finalization. |
| TTS-08 | Repeated stop/cleanup is idempotent; no stale audio queue is flushed after cancellation. |
| TTS-09 | VoxCPM prompt-cache and public fallback paths are exercised through fakes without downloading a model. |
| TTS-10 | CosyVoice remains unreachable from the production selector and is covered only by compatibility tests. |
| TTS-11 | Existing 6 GB GPU preflight and provider cleanup tests remain green. |
| TTS-12 | Empty/failed warmup marks TTS unavailable or returns failure; MainWindow does not report a broken service as ready. |
| TTS-13 | Vox ring-buffer overflow is bounded/rejected without overwriting unread samples. |
| TTS-14 | Cosy missing-prompt and cancellation paths stop/join workers and never flush cancelled buffered chunks. |
| TTS-15 | Queue shutdown cannot create two live workers after a blocked worker times out. |
| TTS-16 | Generation-scoped `PipelineConfig(use_tts=False)` makes zero provider calls. |
| TTS-17 | The enqueue/UI commit barrier cannot leave audio/history for text rejected as stale before visible append. |
| TTS-18 | Starting a new recording and starting report/farewell cancel old pending delivery before new participant-facing audio. |
| TTS-19 | A provider stall cannot leave a partial sentence beyond the configured bounded flush contract. |
| TTS-20 | Queue capacity and generation-record retention follow a bounded stale-drop policy. |
| TTS-21 | AudioStarted/AudioFinished status is observable for deployment telemetry but cannot change business state. |

### 6.4 Cross-boundary regression tests

| ID | Test assertion |
|---|---|
| X-01 | `TurnPolicy`, `ScaleRuntime`, `SessionEngine`, and `needs_rag` authority tests remain green and unchanged. |
| X-02 | A stale TTS callback cannot mutate `DeliveryLedger`, history, DataManager, scale state, lifecycle state, or RAG. |
| X-03 | A new user turn cancels old audio without changing report-first farewell ordering. |
| X-04 | Two consecutive sessions have no recording queue, TTS status, or provider-stop leakage. |
| X-05 | vLLM ports `:8000`/`:8001`, A100 launch/profile tests, and Ollama compatibility remain green. |

### 6.5 Real-device acceptance log (not unit-test pass)

The following remain **NOT RUN / environment unavailable** until the actual
deployment machine and devices are available:

```text
microphone startup recovery
quiet speech / background noise / long utterance / low-volume sentence tail
20–30 consecutive recording turns and tail-loss count
FSMN-VAD endpoint latency and false-stop/false-continue rate
VoxCPM2 first-audio latency at 0.8/0.5/0.3 s prebuffer candidates
underruns and stop latency after one and two rapid interruptions
stale generation audio after a new user turn
two-session audio/model/queue cleanup
```

Each real run must record environment, model/provider versions, device names,
sample rates, measured latency, failures, and raw log paths. A fake-backed
test result must never be copied into this section.

## 7. Verification gate before each production commit

Before any hardening commit:

```text
git status --short
git diff --check
affected tests
full python -m pytest tests -q
static search for direct TTS threads, swallowed live provider errors,
RMS/VAD duplicate owners, and production CosyVoice selection
```

The expected full-suite baseline is **517 passed, 1 skipped, 0 failed** until
the first code change. Any new failure is a stop condition, not a reason to
modify a frozen architecture layer.

## 8. Freeze acceptance

This design is accepted only if all of the following remain true:

1. The inventory identifies the actual production ASR/TTS entry points and
   distinguishes them from future-facing protocols.
2. Recording shutdown is specified as sentinel/drain, not a flag-only loop.
3. Microphone failures have an explicit UI feedback contract.
4. FSMN-VAD/hotwords require verified installed APIs and no partial-ASR UI.
5. TTS has explicit completed/cancelled/failed semantics and no swallowed live
   errors.
6. VoxCPM2 remains the only production backend; CosyVoice is contained.
7. Phase 7 generation/queue/ledger semantics remain unchanged while the
   provider status/error boundary is hardened.
8. The real hardware smoke section is explicitly NOT RUN until available.
9. No production file is modified by this freeze; implementation starts only
   in the separate commits listed above.

After this documentation-only freeze, the first allowed production task is
`fix: make stt recording shutdown lossless`.
