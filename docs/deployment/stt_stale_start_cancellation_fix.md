# STT Stale-Start Cancellation Fix

Status: ASR-13 fixed; the frozen STT lifecycle suite is now green.

## Scope and baseline

- Branch: `codex/a100-vllm-safety`
- Starting commit: `7238e5581c57824bf47817bc73dcd4f4c1bb0716`
- Target change: `fix: cancel stale stt startup attempts`
- Production file: `services/stt_service.py`
- Tracked regression: `tests/test_stt_startup_failure.py`
- Frozen acceptance suite: `tests/test_stt_recording_lifecycle.py` (kept
  uncommitted for the later lifecycle-test commit).

## Race and ownership fix

The deterministic ASR-13 sequence blocked `sd.InputStream(...)`, called
`stop_recording()`, and then released the blocked start.  Previously the stale
thread could publish and start its stream and return `True` after its state had
already been retired.

Startup now keeps the newly constructed stream as a local candidate.  It checks
the recording identity and cancellation state before and after the potentially
blocking `candidate_stream.start()` call.  Only a still-current, uncancelled
attempt atomically publishes the stream and collector under the existing
`recording_state.lock -> _recording_state_lock` order.  A stale candidate is
closed outside the locks and returns `False`; it is not reported as a
`RecordingStartError` and cannot overwrite a newer attempt.

No PortAudio blocking operation is performed while lifecycle locks are held.
The accepted sentinel/drain path, FSMN-VAD/RMS endpoint semantics, and ASR-05
idempotent stop behavior remain unchanged.

## Verification

- ASR-13 focused reproducer and tracked regression: `2 passed`.
- Frozen lifecycle suite: `16 passed, 0 failed`.
- STT startup/cleanup/FSMN slice: `22 passed`.
- Tracked full regression excluding the intentionally uncommitted lifecycle
  suite: `537 passed, 1 skipped, 0 failed`.
- The one skip is the known local PySide6/QtWidgets DLL limitation.
- Real microphone/audio-device/A100 validation: `NOT RUN / environment unavailable`.
