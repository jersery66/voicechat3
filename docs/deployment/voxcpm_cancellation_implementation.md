# VoxCPM2 Cancellation Semantics Implementation

## Scope and baseline

This deployment-hardening change fixes cancellation of the production
VoxCPM2 explicit `sounddevice.OutputStream`.  It does not change STT/FSMN-VAD,
TurnPolicy, TurnDecision, ScaleRuntime, SessionEngine, RAG, prompts, model
profiles, sentence segmentation, delivery authority, or CosyVoice.

- Branch: `codex/a100-vllm-safety`
- Starting HEAD: `6a5cd70937af254af1dc53631028db2bf57d616f`
- Baseline regression: **569 passed, 1 skipped, 0 failed**
- Previous accepted TTS status commit: `8a0510d701383a6a16912508e0abf4de2153aa34`
- Real VoxCPM2/audio-device/A100 smoke: **NOT RUN / environment unavailable**

The installed development environment reports `voxcpm==2.0.2`.  No real
VoxCPM2 model was loaded during this change.  The configured deployment
markers remain unchanged: model source `OpenBMB/VoxCPM2`,
`VOXCPM_CFG_VALUE=2.0`, `VOXCPM_INFERENCE_TIMESTEPS=10`, and the existing voice
prompt path/configuration.

## Defect fixed

The previous implementation used an explicit `sd.OutputStream` for streaming
playback, but `stop_playing()` only set a shared boolean and called the module
function `sd.stop()`.  That function does not own or reliably abort an
explicit stream.  A cancelled request could therefore continue producing
audio, retain unread ring-buffer data, or be misreported after a late provider
or worker callback.

## Request-local state and ownership

Each `generate_and_play()` request now owns a `_PlaybackState` containing:

- `cancel_event` and `done_event`;
- the request's explicit output stream;
- the provider generator;
- the stream worker thread.

`TTSService` keeps one active-state reference protected by a separate
`_active_playback_lock`.  Publish and clear operations are identity-checked,
so a finishing old request cannot clear or overwrite a newer request's state.
The serial `_play_lock` is retained for provider/playback ordering, while
`stop_playing()` never acquires it.  A new public request cancels the active
request before waiting for that lock.

## Cancellation and stream behavior

- `stop_playing()` is thread-safe, idempotent, and non-business logic.
- It sets the request cancellation event, captures the active stream and
  generator under the state lock, then calls stream `abort()` and generator
  `close()` outside the lock on a best-effort basis.
- `sd.stop()` is not used to cancel an explicit request-local stream.  It is
  retained only for the legacy `sd.play()` convenience path and cleanup when
  no explicit request is active.
- A stream is published only after construction.  If cancellation wins the
  publication race, the stream is aborted/closed and playback is never
  entered.
- The callback checks cancellation before reading the ring buffer, writes
  silence, and raises `sd.CallbackStop`; unread samples are not intentionally
  drained after cancellation.
- Stream-worker waits use the request cancellation event.  Provider loops for
  both the prompt-cache generator and `generate_streaming()` check the same
  event between chunks.
- Normal completion remains `PlaybackStatus.COMPLETED`; a requested/stale
  cancellation is `CANCELLED`; provider, stream, worker, or timeout errors are
  `FAILED`, with cancellation taking precedence over an expected abort error.

## Tests and verification

Added `tests/test_tts_cancellation.py` with deterministic stream/provider
fakes covering active-stream publication and abort, cancellation before stream
construction, lock-safe public cancellation, module-level-stop isolation,
idempotent stop, unread-buffer invalidation, stale-state identity protection,
and next-request recovery.  The existing completion-status tests continue to
cover normal, empty, zero-audio, provider-error, worker-error, and cancellation
result mapping.

- Focused completion/cancellation/preflight slice: **27 passed**.
- Full regression after implementation: **577 passed, 1 skipped, 0 failed**.
- Known skip: local PySide6/QtWidgets DLL load failure; no new skip was added.
- `git diff --check`: passed.
- Real cancellation latency, real audio interruption, A100/vLLM, and model
  smoke: **NOT RUN / environment unavailable**.  Mocked tests are not reported
  as hardware validation.

## Scope guard

No STT, FSMN-VAD, ASR model/API, hotword, prompt, business-policy, RAG,
sentence-segmentation, delivery-authority, or CosyVoice production changes are
included.  The next separately frozen hardening item remains the TTS
cancellation/failure regression suite; no follow-on hardening item is started
by this commit.

## Finalization

The production implementation commit and remote verification are recorded
after the implementation commit is created and pushed:

- Implementation commit: pending commit creation (`fix: harden voxcpm cancellation semantics`).
- Remote branch: `origin/codex/a100-vllm-safety`.
- Remote synchronization: to be verified after push (`ahead/behind = 0/0`).
- Working tree: to be verified clean after push.
