# GenerationController retention hardening

## Scope and baseline

This deployment-hardening change addresses only TTS-20a: transient
`GenerationRecord` objects previously accumulated for the lifetime of the
process. It does not change generation authority, sentence-queue capacity,
SentenceSegmenter timing, TTS providers, or any business/session authority.

- Starting HEAD: `c50b8f5c5ff348200347ed282be64cf3462a65b4`
- TTS-20b baseline: FIXED / ACCEPTED
- Frozen acceptance file remains intentionally untracked and uncommitted:
  `tests/test_tts_hardening_acceptance.py`

## Retention contract

- `DEFAULT_MAX_GENERATION_RECORDS = 64` is a fixed transient grace window.
- `GenerationController(max_records=...)` accepts positive test bounds but is
  not a participant/UI configuration.
- After each allocation, the oldest non-current generation IDs are pruned
  deterministically until the bound is satisfied.
- The newly allocated current generation is never pruned, generation IDs are
  never reset or reused, and no tombstone collection is introduced.
- Cancelled and finalized records remain available until normal pruning so
  short-lived late callbacks can fail closed through `get_record()` without
  coupling delivery retention to durable history.

## Callback and history safety

Pruning occurs under the controller lock. Cancellation events and listener
notifications are captured before pruning and delivered outside the lock, so a
superseded record may be pruned without suppressing its cancellation event.
After pruning, `get_record()` returns `None`, `is_current()` is false, and
`cancel_generation()` returns false; ledger operations therefore perform no
late UI/history/DataManager mutation. Already-persisted assistant history is
unchanged when its transient generation record is later removed.

## Verification

- Retention tests: **16 passed**.
- Focused TTS/queue/retention slice: **124 passed**.
- Frozen acceptance: **32 passed / 2 expected failed**. Remaining failures are
  TTS-14 CosyVoice cancelled-buffer flush and TTS-19 provider-stall bounded
  sentence flush.
- Full tracked regression, excluding the intentionally untracked frozen
  acceptance file: **636 passed / 0 failed**.
- `git diff --check`: PASS.
- Real VoxCPM2/audio/A100 validation: **NOT RUN / environment unavailable**.

## Boundaries

No changes were made to `SentenceDeliveryQueue` capacity/lifecycle,
`GenerationController` authority semantics, `services/pipeline.py`,
`services/tts_service_cosyvoice.py`, `services/tts_service_voxcpm.py`, STT,
FSMN-VAD, TurnPolicy, ScaleRuntime, SessionEngine, RAG, prompts, or model
profiles.

Implementation commit: `fix: bound delivery generation retention`.
