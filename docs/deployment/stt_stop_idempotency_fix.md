# STT Stop Idempotency Fix

Status: accepted narrow fix for ASR-05; ASR-13 remains intentionally
unresolved for a separate change.

## Scope and baseline

- Branch: `codex/a100-vllm-safety`
- Starting commit: `57f8b1af83a40622f00997583a1c26f8dfda8b08`
- Target change: `fix: make stt stop idempotent`
- Production file: `services/stt_service.py`
- Narrow regression: `tests/test_stt_cleanup.py`
- Frozen cross-cutting lifecycle suite remains intentionally uncommitted at
  `tests/test_stt_recording_lifecycle.py`.

## Confirmed root cause and ownership change

After a recording was drained, `stop_recording()` had no current
`_RecordingState` but returned the historical `self.recorded_audio` alias.
The second stop therefore returned the previous utterance again.  The no-state
path now returns an empty `np.ndarray`, and the compatibility queue/list
aliases are detached only after the first completed recording has been captured
locally for its one permitted return.

The sentinel/drain order, stream-close location, FSMN-VAD/RMS fallback
selection, and manual stop behavior are unchanged.

## Verification

- ASR-05 focused proof: `2 passed` (frozen reproducer plus tracked regression).
- Frozen lifecycle suite: `15 passed, 1 failed`; the only failure is the
  expected ASR-13 stale-start race.
- Tracked regression excluding the intentionally red uncommitted lifecycle
  suite: `536 passed, 1 skipped, 0 failed`.
- The one skip is the known local PySide6/QtWidgets DLL limitation.
- `git diff --check`: passed.
- Real microphone/audio-device validation: `NOT RUN / environment unavailable`.

## Deferred defect

ASR-13 remains confirmed: if `start_recording()` is blocked while creating the
input stream, a concurrent `stop_recording()` can retire the recording state,
after which the stale start thread still starts the stream and returns success.
That race is deliberately not changed by this commit and requires a separate
narrow production fix.
