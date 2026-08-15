# STT Recording Lifecycle Acceptance

Status: **ACCEPTED**

This is a deployment-hardening acceptance record after the eight architecture
phases.  It freezes deterministic STT recording behavior; it is not a new
architecture phase and does not claim real-device ASR accuracy.

## Test history

The frozen suite first ran against `57f8b1af83a40622f00997583a1c26f8dfda8b08`:

- `14 passed, 2 failed`
- ASR-05 exposed repeated `stop_recording()` returning old audio.
- ASR-13 exposed a stale startup thread reopening/publishing an old stream.

The defects were fixed independently:

- `7238e5581c57824bf47817bc73dcd4f4c1bb0716` — `fix: make stt stop idempotent`
  — lifecycle result `15 passed, 1 failed` (ASR-13 only).
- `362a4fc0a34f3aeefca5cf6418da1efbfb20753a` —
  `fix: cancel stale stt startup attempts` — lifecycle result
  `16 passed, 0 failed`.

## Contract map

| Contract | Acceptance coverage |
|---|---|
| ASR-01 | `test_asr01_clean_start_creates_fresh_recording_owners` — fresh state, queue, list, collector, stream, and VAD reset. |
| ASR-02 | `test_asr02_start_failure_cleans_resources_without_active_recording` plus `tests/test_stt_startup_failure.py` — typed startup failure and cleanup. |
| ASR-03 | `test_asr03_final_callback_frame_is_preserved_in_order` — final callback frame remains in returned PCM. |
| ASR-04 | `test_asr04_manual_stop_uses_one_sentinel_and_drains_queue` — one sentinel, ordered drain, collector exit, and stream close. |
| ASR-05 | `test_asr05_stop_and_cleanup_are_idempotent` plus the tracked cleanup regression — audio is consumed exactly once. |
| ASR-06 | `test_asr06_fsmn_endpoint_stops_once_and_repeated_end_is_stale` — one automatic stop and stale repeated END ignored. |
| ASR-07 | `test_asr07_fsmn_is_single_endpoint_owner_and_rms_is_fallback_only` and `test_asr07_failed_fsmn_load_selects_only_rms_fallback`. |
| ASR-08 | `test_asr08_completed_utterance_calls_final_asr_once_without_partial_path` — one final transcription, no partial path. |
| ASR-09 | `test_asr09_current_final_asr_outcomes_are_deterministic` — empty, malformed, and provider-error behavior as currently implemented. |
| ASR-10 | `test_asr10_lifecycle_change_does_not_add_hotwords_or_text_rewrites` — no lifecycle-added hotword or correction behavior. |
| ASR-11 | The same lifecycle static boundary confirms `_correct_common_errors` is not part of new VAD/start-stop code; existing ASR/scoring tests remain green. |
| ASR-12 | `tests/test_stt_cleanup.py`, `tests/test_stt_startup_failure.py`, `tests/test_fsmn_vad.py`, and voice protocol tests remain green. |
| ASR-13 | `test_asr13_stop_during_device_open_does_not_allow_late_success` plus the tracked startup regression — cancelled startup returns `False`, closes the candidate, and leaves no collector. |
| ASR-14 | `test_asr14_fsmn_manual_stop_and_cleanup_share_one_close_transition` — FSMN, manual stop, and cleanup converge safely. |
| ASR-15 | `test_asr15_rapid_restart_isolates_old_state_and_vad_event` — old state/audio/VAD cannot affect the new recording. |

## Production invariants locked

- Recording-local queue, frame list, and state ownership.
- Lossless sentinel/drain shutdown and stream close outside the callback.
- Idempotent one-time audio consumption.
- Explicit `RecordingStartError` for genuine startup failures.
- Stale startup cancellation returns `False` without a participant-facing
  microphone error.
- FSMN-VAD primary endpointing with RMS fallback only.
- Manual stop independent of VAD permission.
- Final transcript only; no partial ASR UI path.
- Stale recording and VAD-event isolation across rapid restarts.

## Verification

- Lifecycle focused suite: `16 passed, 0 failed`.
- STT/voice affected slice: `41 passed`.
- Full regression including lifecycle suite: `553 passed, 1 skipped, 0 failed`.
- Skip: local PySide6/QtWidgets DLL limitation.
- `git diff --check`: passed.

## Hardware status

- Real microphone: `NOT RUN / environment unavailable`.
- Real FSMN checkpoint inference: `NOT RUN / environment unavailable`.
- A100/vLLM/STT hardware validation: `NOT RUN / environment unavailable`.

Deterministic fake-backed tests do not constitute hardware or ASR-accuracy
acceptance.  Real-device deployment validation remains pending.
