# Phase 7 implementation: cancellable sentence streaming TTS

## Status and baseline

- Phase 7 design baseline: `38d16ecf2cf59f1d0c74a90b2245b0ebc6e19425`
  (`docs: add phase 7 streaming tts plan`).
- Baseline before production edits: **450 passed, 0 failed, 0 skipped**.
- Scope remained limited to delivery generation, sentence streaming, TTS
  cancellation, and delivered-history coordination. Phases 1–6 were not
  reopened and Phase 8 was not started.

## Generation contract

- Added `conversation/delivery.py` with one `GenerationController`, monotonic
  `generation_id`, cancellation events, generated/delivered ledgers, immutable
  delivery events, and exactly-once history finalization.
- A new user turn cancels the prior generation idempotently, drains queued
  sentences, and invokes `stop_playing()` best-effort. Provider chunks, UI
  events, TTS work, playback callbacks, replacements, and history all reject
  stale ids.
- `generated_text` is diagnostic only. `delivered_text` is the text committed
  to the visible chat bubble and is the only assistant text persisted to
  conversation history/DataManager.

## Sentence streaming and TTS

- `SentenceSegmenter` emits ordered `SentenceReady(generation_id, seq, text)`
  events for `。！？!?．`, repeated terminal punctuation, `……`, safe bounded
  phrase cuts, and final trailing text.
- Configured bounded values for this implementation are `max_chars=80`,
  `max_wait_ms=800`, and `min_stable_chars=4`; provider-specific latency
  measurement is **NOT RUN / environment unavailable** with production TTS.
- One `SentenceDeliveryQueue` worker serializes sentence playback. It checks
  generation freshness before and after the blocking TTS call and continues
  safely after per-sentence synthesis errors.
- VoxCPM2/CosyVoice adapters, internal audio streaming, `generate_and_play`,
  `stop_playing`, vLLM/Ollama endpoints, FunASR, VAD, and A100 launch/profile
  code were not changed.

## Pipeline, UI, history, and auxiliary paths

- Live pipeline streaming now assembles provider chunks and emits stable
  sentence events before provider completion; it no longer submits a whole
  response to a direct TTS thread/future.
- `ChatPanel` rejects stale, duplicate, and out-of-order generation/sequence
  events. Visible UI append is the delivery commit point.
- LLM adapters accept `commit_history=False` for live generations. The
  delivery ledger finalizes delivered history exactly once and suppresses
  phantom assistant turns when no text became visible.
- Opening greetings, post-relaxation replacements, fallback speech, exit
  feedback, and report-first farewell delivery use the same generation/queue
  boundary. Farewell audio remains after report/PDF persistence.
- User history trimming and provider compatibility remain intact; no
  generation state was moved into SessionEngine or TurnPolicy.
- Historical/report-only helpers retain their legacy callback surface for
  compatibility, but the MainWindow live participant path uses only typed
  generation events and the shared queue.

## Verification

- Focused Phase 7 + pipeline/LLM/E2E tests: **90 passed**.
- Full regression using the available model/runtime environment:
  `PYTHONPATH=E:\Anaconda\envs\voice_chat\Lib\site-packages`
  with `E:\Anaconda\python.exe -m pytest tests -q`:
  **471 passed, 0 failed, 1 skipped**. The single skip is the existing
  headless Qt import guard because the local PySide6 DLL is unavailable.
- `python -m py_compile` passed for all changed Python modules.
- `git diff --check` passed.
- Real A100/vLLM 72B/3B, FunASR, and VoxCPM2/CosyVoice production smoke:
  **NOT RUN / environment unavailable** on the current development desktop;
  no model-backed result is inferred from mocks.

## Final Git result

- Implementation commit: `4858cd9a0a45228ba86e48ef97d0eddd43c4ad0d`
  (`feat: enable cancellable sentence streaming tts`).
- Pushed remote HEAD: **NOT UPDATED / network unavailable**. Two push
  attempts using the required no-proxy command could not connect to GitHub;
  the local tracking ref remains the Phase 7 design baseline
  `38d16ecf2cf59f1d0c74a90b2245b0ebc6e19425` until connectivity returns.
- Local final working-tree status: clean after the implementation and record
  commits; local branch is ahead of the remote by the unpushed commits.
