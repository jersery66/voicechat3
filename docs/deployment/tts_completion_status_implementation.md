# TTS Completion Status Implementation

## Scope

This hardening change makes the production VoxCPM2 playback contract explicit.
It does not modify the accepted STT/FSMN-VAD lifecycle, TurnPolicy,
ScaleRuntime, SessionEngine, prompts, RAG, model profiles, sentence
segmentation, or the broader VoxCPM cancellation design.

Starting baseline:

- Branch: `codex/a100-vllm-safety`
- Starting HEAD: `b9bfb33fb373b7a32d71f62c542de54b65636d1d`
- Baseline regression: `553 passed, 1 skipped, 0 failed`

## Contract

`adapters.tts_results` is the canonical result module. `PlaybackStatus` has
exactly three terminal values:

- `COMPLETED`: audio generation and playback returned normally.
- `CANCELLED`: playback was stopped before normal completion.
- `FAILED`: text, generation, output, provider result, or worker handling
  failed.

`PlaybackResult.ok` is true only for `COMPLETED`. A missing, legacy, or
unknown provider return value is mapped to `FAILED`; it is never treated as a
successful playback.

`TTSBackend.generate_and_play()` now declares `PlaybackResult`. `AudioFinished`
contains the explicit `status` and keeps a read-only `.ok` compatibility view.
The sentence delivery queue continues to suppress all stale-generation
completion events.

## VoxCPM2 implementation

`services/tts_service_voxcpm.py` now reports:

- empty preprocessed text as `FAILED / empty_text`;
- provider generation exceptions as `FAILED` with a bounded internal reason
  and logged traceback;
- output worker exceptions and join timeouts as `FAILED` after the worker join;
- zero generated samples as `FAILED / no_audio`;
- an externally stopped request as `CANCELLED / stopped`;
- non-empty audio that reaches normal completion as `COMPLETED`.

The existing VoxCPM2 streaming generator, PortAudio callback, play lock, and
`stop_playing()` hook remain in place. CosyVoice remains experimental and is
not activated by the production switcher.

## Tests and verification

Added `tests/test_tts_completion_status.py` covering the exact enum, queue
mapping (including `None`, unknown status, exceptions, and all three outcomes),
stale callbacks, failure isolation, normal/empty/zero-audio/provider-error/
worker-error VoxCPM paths, and cancellation before normal completion. Existing
Phase 7 delivery fakes now return explicit `COMPLETED` results.

Focused and compatibility slice:

- `111 passed, 0 failed`

Full regression after the change:

- `569 passed, 1 skipped, 0 failed`
- The single skip remains the known local PySide6/Qt DLL environment issue.

Real VoxCPM2, audio-device, A100/vLLM, FunASR, and deployment smoke were not
run in this environment: **NOT RUN / environment unavailable**. Mocked tests
are not presented as real hardware validation.

## Finalization

- Implementation commit: `fix: make tts completion status explicit`
- Remote push: pending until the code commit is created and verified.
- Working tree status: to be recorded after commit/push verification.
