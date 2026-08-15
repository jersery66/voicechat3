# Streaming sentence deadline flush

## Scope

- Starting HEAD: `0f6788de5193e99c172c5ee643194b57e7a74608`
- Change: make the configured streaming sentence deadline effective when the
  dialogue provider stalls between chunks.
- Commit: `fix: flush stalled streaming sentences on deadline`

This change is limited to the Phase 7 delivery path. It does not change
TurnPolicy, TurnDecision, ScaleRuntime, SessionEngine, RAG, provider transport,
or either TTS provider.

## TTS-19 reproducer

The generation-scoped `_stream_llm()` path previously called
`SentenceSegmenter.flush_if_due()` only at the top of the provider-chunk loop.
If the provider yielded a partial sentence and then blocked in `next(chunks)`,
the loop could not run again and the existing `max_wait_ms=800` deadline had no
independent caller. The buffered text therefore waited for a later provider
chunk or final stream completion.

## Implementation

`ConversationPipeline` now owns at most one lazily-started, pipeline-lifetime
sentence-flush watchdog. A single active registration contains the generation,
segmenter, emission callback, synchronization lock, and deadline. Registering a
new generation replaces the old registration; cleanup uses registration object
identity so a late old finalizer cannot clear a newer generation.

The watchdog waits on a condition until the active segmenter's deadline is due.
It invokes `flush_if_due()` on the same `SentenceSegmenter` used by the
provider thread. Feed, deadline flush, and final flush all hold the
registration lock while mutating the segmenter and emitting through the
existing `_emit_generation_sentence()` path. This preserves sequence order and
keeps UI, delivery ledger, bounded sentence queue, and TTS behavior unchanged.

There is no timer thread per generation and no provider-reader thread. The
watchdog is stopped with a bounded join during pipeline shutdown. The
production `max_wait_ms=800` default and `min_stable_chars=4` behavior are
unchanged; tests inject a short deadline only through the segmenter factory
seam.

Deadline output is rejected when the generation is no longer current. A
cancelled or replaced generation therefore cannot flush stale text. If the
provider resumes after a deadline flush, the original segmenter continues and
sequence numbers remain monotonic without duplicating the flushed prefix.

## Verification

- Focused deadline tests: 15 passed.
- TTS hardening acceptance: 33 passed, 1 expected failure (TTS-14 CosyVoice
  cancelled-buffer flush remains RED).
- Required focused regression slice: 139 passed.
- Full tracked regression (excluding the intentionally uncommitted frozen
  acceptance suite): 651 passed, 0 failed.
- Real vLLM/A100/VoxCPM/audio deployment smoke: **NOT RUN / environment
  unavailable**. The deterministic tests verify scheduling and stale-generation
  semantics only.

TTS-14 remains the only frozen acceptance failure. No TTS-14 or later hardening
work was started in this change.
