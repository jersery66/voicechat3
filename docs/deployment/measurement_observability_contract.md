# Measurement and observability contract

Batch 3 adds passive measurement infrastructure only.  It does not set a
performance target, select a model, or change the frozen conversation
authority chain.

## Evidence types

Every measurement is explicitly one of:

- `MEASURED` — obtained from a real runtime clock, API usage response,
  `nvidia-smi`, or device response.
- `SIMULATED` — produced by a deterministic unit/mock test.
- `NOT AVAILABLE` — the source could not provide a reliable value.

Missing values remain `null`; they are never replaced with character counts,
estimates, or hardware assumptions.

## Timing events

`TimingRecorder` uses `time.perf_counter_ns()` by default.  Wall-clock UTC is
used only for artifact timestamps.  The contract distinguishes:

```text
llm_request_start -> llm_first_token -> first_sentence_ready -> llm_generation_end
speech_end -> asr_final_text
speech_end -> llm_first_token
speech_end -> llm_first_sentence
speech_end -> playback_start / tts_first_audio_ready
turn_start -> playback_end
```

`llm_first_sentence` remains available as the model-side boundary; when the
sentence delivery layer can provide it, `first_sentence_ready` is preferred for
the participant-facing first-sentence metric.  The recorder also supports VAD,
policy, RAG, TTS, and playback boundaries.  A cancelled
or failed generation is not a successful performance sample.

## Token usage

`completion_tokens` and `tokens_per_second` are populated only from real API
or server usage.  When usage is absent they remain unavailable.  Chinese
characters, bytes, or output length are not token proxies.

## Memory snapshots

`memory_snapshot.py` reads `nvidia-smi` without applying a threshold.  It
records GPU identity, total/used/free memory, and a process list slot.  The
initial artifact state is `NOT RUN`; a missing command is `NOT AVAILABLE`.

## Structured events and privacy

`StructuredEventWriter` writes identity, status, timing, and stable error-code
metadata to JSONL.  Participant prompts, responses, transcripts, audio,
clinical text/scores, and hidden reasoning are excluded by default.  Content
logging is `OFF`.

The error taxonomy classifies failures; it does not prescribe retries,
fallbacks, score changes, or business recovery.

## Artifacts and hardware boundary

`scripts/deployment/measurement.py` initializes:

```text
test_output/observability/
  measurement_events.jsonl
  memory_snapshots.jsonl
  performance_summary.json
  observability_summary.json
```

The initial summaries are `NOT RUN` and are ignored by Git.  Real RTX PRO 6000
performance, VRAM, TTFT, first-audio, and E2E claims remain `NOT RUN` until
the target workstation supplies measured evidence.

`build_performance_summary()` includes only `SUCCESS` + `MEASURED` samples in
the real-performance aggregate.  It never compares baseline/candidate or
emits a winner/promotion decision.  When samples exist its status is
`MEASURED`, not `PASS`; this layer defines no performance thresholds.
