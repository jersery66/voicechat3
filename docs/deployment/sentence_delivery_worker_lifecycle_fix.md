# SentenceDeliveryQueue worker lifecycle fix

## Scope

- Starting HEAD: `069eb6738e282ded75c459252225a3fdd35ca1ae`
- Target commit: `fix: prevent duplicate sentence delivery workers`
- Production scope is limited to `conversation/delivery.py` and its worker
  lifecycle regressions.  VoxCPM2, CosyVoice, the pipeline, STT, and business
  policy are unchanged.

## TTS-15 reproducer

The previous `shutdown(timeout)` set the shared stop event, joined the worker,
and then unconditionally assigned `self._thread = None`.  If the provider was
blocked beyond the timeout, the old worker was still alive but its ownership
reference had been discarded.  A subsequent `start()` cleared the shared stop
event and created a second `sentence-tts-worker`, allowing both workers to
run.

## Fix

`SentenceDeliveryQueue` now keeps one authoritative worker reference and a
stopping marker.  `start()` returns while the referenced worker is alive and
never clears its stop event.  A dead reference is retired only after
`is_alive()` is false, so a later restart can create exactly one new worker.

Shutdown no longer relies on a `None` sentinel.  The bounded queue polling loop
observes the stop event, avoiding stale shutdown markers on a later restart.
New sentence work is rejected while shutdown is pending.  The active provider
is interrupted best-effort after the bounded join attempt; provider stop
errors are contained.  A still-live worker reference remains authoritative,
and late `AudioFinished` events are suppressed after shutdown.

## Verification

- Worker lifecycle regressions: **12 passed**
- Frozen acceptance suite: **30 passed, 4 failed**.  Remaining failures are
  intentionally outside TTS-15: TTS-14, TTS-19, and the two TTS-20 checks.
- Full tracked regression (excluding the intentionally uncommitted frozen
  acceptance file): **607 passed, 0 failed**.
- `git diff --check`: **PASS**.
- Real VoxCPM2/audio/A100 validation: **NOT RUN / environment unavailable**.

## Files

- `conversation/delivery.py` — single-worker ownership, shutdown state, stale
  callback protection, and restart handling.
- `tests/test_sentence_delivery_worker_lifecycle.py` — deterministic worker
  lifecycle tests using Events and bounded joins.
