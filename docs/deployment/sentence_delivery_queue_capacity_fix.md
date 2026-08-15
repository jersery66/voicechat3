# Sentence delivery queue capacity hardening

## Scope

This deployment-hardening change addresses only TTS-20b: the live
`SentenceDeliveryQueue` previously used an unbounded `queue.Queue`, so a slow
or stalled TTS provider could let pending sentence work grow without limit.
TTS-14, TTS-19, and GenerationController retention (TTS-20a) remain outside
this change.

## Baseline and contract

- Starting HEAD: `d740fd85dec15a2055b19278ecc88844ae790aae`
- Existing frozen acceptance before this change: 30 passed / 4 expected failed
- The frozen acceptance file remains intentionally untracked and uncommitted:
  `tests/test_tts_hardening_acceptance.py`
- Default capacity: `DEFAULT_MAX_PENDING_SENTENCES = 32`
- Explicit capacities must be positive; tests use capacities of 2–4.

## Implementation

- `SentenceDeliveryQueue` now constructs `queue.Queue(maxsize=...)`.
- Admission uses `put_nowait()` and never blocks the LLM/pipeline producer.
- When full, the queue is drained under the queue ownership lock, stale
  generations are discarded using `GenerationController.is_current()`, and
  retained current-generation items are reinserted in FIFO order before a
  single retry.
- A current sentence is never evicted to admit another current sentence. If
  the bounded queue is still full, admission returns `False`.
- `_next_seq[generation_id]` advances only after successful admission, so a
  rejected sentence can be retried with the same sequence number.
- Queue rebuild/removal paths call `task_done()` for every removed item and
  re-account retained items on reinsert; cancellation also releases that
  generation's sequence bookkeeping.
- Shutdown drains pending queue items without a sentinel or blocking put,
  while preserving the TTS-15 single-worker ownership and cancellation rules.

## Verification

- Queue-capacity tests: **13 passed**.
- TTS-15 and related focused regression slice: **95 passed**.
- Frozen acceptance: **31 passed / 3 expected failed**. Remaining failures are
  TTS-14 CosyVoice cancelled-buffer flush, TTS-19 provider-stall bounded flush,
  and TTS-20a GenerationController retention.
- Full tracked regression, excluding the intentionally untracked frozen
  acceptance file: **620 passed / 0 failed**.
- `git diff --check`: PASS.
- Real VoxCPM2/audio/A100 validation: **NOT RUN / environment unavailable**.

## Boundaries

No changes were made to GenerationController retention, SentenceSegmenter
flush scheduling, STT/FSMN-VAD, TurnPolicy, ScaleRuntime, SessionEngine, RAG,
prompts, model profiles, or TTS provider implementations.

Implementation commit: `fix: bound sentence delivery queue`.
