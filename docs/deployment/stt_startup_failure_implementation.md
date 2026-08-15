# STT Microphone Startup Failure Implementation

Status: implemented as the second deployment-hardening change.

## Scope and baseline

- Branch: `codex/a100-vllm-safety`
- Starting commit: `b9ddbb9c1cf2286746c39125445ef109fb96f030`
- Change: `fix: propagate microphone startup failures`
- Production files: `services/stt_service.py`, `ui/main_window.py`.
- Focused regression file: `tests/test_stt_startup_failure.py`.

The previous STT startup path caught device and PortAudio errors inside the
background thread, leaving the UI in its `正在录音...` state.  A failed start
could therefore look like a valid recording attempt.

## Failure contract

`services.stt_service.RecordingStartError` is now the explicit startup error
contract.  `STTService.start_recording()` returns `True` only after
`InputStream.start()` completes.  Any startup failure first converges through
the existing per-recording sentinel/drain cleanup, closes any created stream
outside the audio callback, clears the recording state, logs the original
traceback, and raises `RecordingStartError` chained from the original error.

The contract covers no usable input device, device-query failure,
`InputStream` construction failure, and `stream.start()` failure.  The
lossless shutdown implementation from
`b9ddbb9c1cf2286746c39125445ef109fb96f030` remains unchanged for resources
that were successfully created.

## UI propagation

`MainWindow` now assigns a monotonic recording-attempt ID and starts STT only
through `_start_recording_worker(attempt_id)`.  The worker does not touch Qt
widgets; it logs the technical failure and places a
`("recording_start_failed", attempt_id)` event on `processing_queue`.

The Qt queue handler accepts a failure only when its attempt ID is still the
active attempt.  A current failure clears the UI recording state, resets the
record button without starting a pipeline, and shows the participant-safe
status:

`麦克风启动失败，请检查麦克风连接后重试`

Late failures from a manually stopped attempt or an older attempt cannot
reset a newer recording.  Unexpected provider exceptions also fail closed in
the UI while retaining their traceback in the technical log.

The broader race where a user stops while device opening is still in progress
and the old startup thread later opens a stream is intentionally not solved in
this commit; it remains a separately testable lifecycle-hardening item.

## Verification

- Focused startup-failure tests: `6 passed`.
- Affected STT/voice slice: `31 passed`.
- Full regression: `523 passed, 1 skipped, 0 failed`.
- The single skip remains the known local PySide6/QtWidgets DLL environment
  limitation.
- `git diff --check`: passed.
- Real microphone/FunASR/A100 smoke: `NOT RUN / environment unavailable` on
  the current development machine.  No fake-backed result is reported as
  hardware validation.

## Scope guard

No FSMN-VAD integration, AutoModel migration, hotwords, ASR correction
redesign, TTS/provider changes, prompt/model changes, or Phase 1–8 authority
changes are included.  The next frozen item is:

`refactor: integrate fsmn vad for utterance endpointing`
