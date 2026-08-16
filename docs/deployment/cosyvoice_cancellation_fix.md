# CosyVoice cancellation semantics hardening

## Scope and baseline

- Starting HEAD: `16f92e1812c4553d63f11d6a535768598e668f70`
- Change: `fix: harden cosyvoice cancellation semantics`
- Compatibility-only backend: production selection remains unchanged and
  CosyVoice is still unreachable from the production TTS selector.
- VoxCPM2 remains the only production TTS backend.

This change is limited to `services/tts_service_cosyvoice.py` and its focused
compatibility tests. It does not modify VoxCPM2, delivery infrastructure,
Pipeline, STT/FSMN-VAD, prompts, RAG, or business authority.

## TTS-14 defects fixed

The former playback worker flushed its unread pre-buffer whenever it left the
loop. That treated cancellation, provider failure, and normal EOF identically,
so audio that had been explicitly cancelled could still be written to the
speaker. The worker now distinguishes a normal EOF sentinel from cancellation
or failure: only normal EOF may flush a remaining pre-buffer; cancellation and
failure discard it and reject further writes.

The former missing-prompt path opened an audio stream and started a worker
before discovering that synthesis kwargs could not be built. Synthesis kwargs
are now validated first, so an invalid prompt creates no stream or worker.

## Request-local ownership and cleanup

Each active request has a `_CosyPlaybackState` containing its stop event, done
event, worker reference, and stream. `stop_playing()` uses a small state lock,
sets only the active request's stop event, and stops/closes that request's
stream without acquiring `_play_lock`. Cleanup is identity-safe: a late
finalizer from request A cannot clear or close a replacement request B.

Queue admission uses bounded timeout retries and re-checks cancellation, so a
full compatibility queue cannot make cancellation unresponsive. Every path
after worker creation converges on a bounded join, best-effort stream close,
and identity-safe active-state cleanup. Repeated stop and cleanup calls are
harmless.

## Verification

- Focused CosyVoice cancellation tests: 13 passed.
- Frozen TTS acceptance: 34 passed, 0 failed.
- Required TTS/delivery regression slice: 155 passed.
- Full tracked regression, excluding the intentionally uncommitted frozen
  acceptance suite: 664 passed, 0 failed.
- Real CosyVoice model/audio playback: **NOT RUN / environment unavailable**.
- Real VoxCPM2/A100/audio deployment smoke: **NOT RUN / environment
  unavailable**.

No production selector change was made; CosyVoice remains compatibility-only.
