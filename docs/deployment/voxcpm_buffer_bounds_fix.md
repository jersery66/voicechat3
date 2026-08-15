# VoxCPM playback buffer bounds fix

## Scope

- Starting HEAD: `103518380bd8ea5d6dead0c7653a29776c15b8ff`
- Target commit: `fix: bound voxcpm playback buffer`
- This change is limited to the VoxCPM2 playback ring buffer and its focused
  regression tests.  CosyVoice, the sentence delivery queue, the pipeline,
  STT, and business policy are unchanged.

## Defect and fix

The previous producer wrote every generated chunk at `write_pos % buf_len`
without accounting for unread samples.  Once production overtook the audio
callback, wrapped writes overwrote audio that had not played yet.

The ring remains a fixed-size buffer (`VOXCPM_PLAYBACK_BUFFER_SECONDS = 120`)
with the invariant:

```text
0 <= write_pos - read_pos <= buf_len
```

Both the prompt-cache and public `generate_streaming` paths now call one
bounded append helper.  It writes only the currently available capacity,
waits on a `Condition` when full, and resumes after the callback advances
`read_pos`.  Chunks larger than the ring are accepted incrementally in order;
there is no overwrite, dropped newest audio, or unbounded replacement queue.

The producer periodically re-checks cancellation while waiting.  An output
worker exception sets a request-local failure event and wakes the producer, so
neither cancellation nor worker failure can leave a blocked generation behind.
The existing `COMPLETED` / `CANCELLED` / `FAILED` result mapping is preserved.

## Verification

- Focused bounded-buffer regressions: **6 passed**
- Frozen acceptance suite: **29 passed, 5 failed**.  The remaining failures
  are intentionally outside TTS-13: TTS-14, TTS-15, TTS-19, and the two TTS-20
  capacity/retention checks.
- Full tracked regression (excluding the intentionally uncommitted frozen
  acceptance file): **595 passed, 0 failed**.
- `git diff --check`: **PASS**.
- Real VoxCPM2/audio/A100 validation: **NOT RUN / environment unavailable**.
  Mocked OutputStream tests do not establish physical underrun or stop-latency
  behavior on the deployment machine.

## Files

- `services/tts_service_voxcpm.py` — bounded condition-backed ring writer.
- `tests/test_voxcpm_buffer_bounds.py` — prompt-cache/public-path ordering,
  cancellation, and worker-failure regressions.
