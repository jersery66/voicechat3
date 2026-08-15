# STT Recording Shutdown Implementation

Status: implemented as the first deployment-hardening change.

## Scope and baseline

- Branch: `codex/a100-vllm-safety`
- Starting commit: `cb069f53a95d984de3a3073cd10590d032d332ac`
- Change: `fix: make stt recording shutdown lossless`
- Production scope: `services/stt_service.py` only, plus this implementation record.
- The temporary red/green reproducer used during TDD was removed before commit;
  no new full ASR lifecycle suite was added.

The defect was a race between the PortAudio callback, `stream.start()`, and the
collector loop.  The previous collector watched the mutable service-level
`is_recording` flag, so a stop request could make it exit before an accepted
final callback frame was drained.

## Implementation

Each recording now owns a private `_RecordingState` containing its queue,
recorded chunks, stream handle, VAD counters, stop state, and collector.  The
historical `audio_queue`, `recorded_audio`, `stream`, and `is_recording`
attributes remain as compatibility aliases for callers.

Shutdown is ordered as follows:

1. A manual or VAD stop acquires the recording lock, stops accepting frames,
   and enqueues exactly one sentinel after all accepted callback frames.
2. The collector consumes every queued frame through the sentinel.
3. The collector closes the stream outside the PortAudio callback.
4. `stop_recording()` joins the collector without a timeout and concatenates
   only after the drain has completed.

Repeated stop requests are idempotent.  Manual stop and the existing energy-
based VAD endpoint use the same sentinel/drain path.  The VAD policy and
thresholds were not changed.  Microphone startup errors are still logged and
swallowed as before; propagating those failures is the next separately scoped
hardening item.

No changes were made to TurnPolicy, TurnDecision, ScaleRuntime,
SessionEngine, TTS providers, prompts, RAG, model endpoints, or any Phase 1–8
authority contract.

## Verification

- TDD red: the temporary synchronous-callback reproducer failed on the
  baseline because the accepted frame was returned as an empty recording.
- TDD green: the same reproducer passed after the sentinel/drain cutover and
  was removed before commit.
- Affected voice/STT slice: `25 passed`.
- Full regression: `517 passed, 1 skipped, 0 failed`.
- `git diff --check`: passed.
- Real microphone/FunASR/A100 smoke: `NOT RUN / environment unavailable` on
  the current development machine.  No mock result is reported as hardware
  validation.

## Next hardening item

The next independent change is:

`fix: propagate microphone startup failures`

It is intentionally not included in this commit.
