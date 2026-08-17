# Post-freeze runtime ordering correction audit

Baseline: `pre-hardware-freeze-20260817` at
`32793d300662fd53d462d30cbebc071348544890`.

This is an audit for Correction A only. It does not reopen the frozen release
tag and does not define a new Phase.

## Current facts

1. Raw ASR normalization is performed by `services.pipeline.correct_asr_text`
   after the transcript enters `ConversationPipeline.execute`. The STT
   provider also has a separate `_correct_common_errors` method for fixed
   domain terms.
2. The pipeline previously rewrote critical phrases including frequency,
   duration, negation, and sleep polarity. The most dangerous example was
   `中途不醒 -> 中途会醒`; this can invert a participant statement.
3. Stable sentences first enter the live delivery path in
   `ConversationPipeline._emit_generation_sentence`, which queues a
   `SentenceReady` and emits the same event to the UI callback.
4. Output cleanup and internal-leak protection currently happen both in
   `core.tags`/`ResponseBuilder` and in the generation completion path. Before
   Correction A, the stable sentence path did not have one explicit guard
   immediately before queue/UI/TTS admission.
5. Active-scale answer interpretation currently occurs after dialogue
   generation in `ConversationPipeline.execute`; `ScaleRuntime.accept_answer`
   is then called from the interpreted result.
6. `TurnPolicy` is the only action authority. `ScaleRuntime` owns item/answer
   transitions. `SessionEngine` remains the lifecycle writer.
7. `TurnPolicy` currently requests `needs_rag=True` for `START_SCALE` and
   `CONTINUE_SCALE`, even though the scale definition/runtime already supplies
   the canonical question context.

## Correction A boundary

The correction will add only semantic observations, a pre-delivery safety
boundary, ordering-preserving scale transitions, and the scale-action RAG gate.
It will not modify model profiles, generation settings, prompts, provider
implementations, Phase 5/A/B semantics, or SessionEngine lifecycle semantics.

## A.1 closure notes

- Clear semantic values such as `中途不醒`, `已经两周`, `每天`, `一周两三天`,
  and `发生过两次` are preserved and do not trigger `CLARIFY_INPUT`.
- Clarification is reserved for malformed or explicitly uncertain phrases such
  as `中途部醒`, `不知道多久`, `频率我记不清`, or `几次记不清`.
- A first unsafe stable sentence is blocked and replaced once by a deterministic
  action/runtime-derived fallback through the same UI/TTS delivery path.
- A later question-budget overflow is dropped without an additional fallback.
- Stale/cancelled generations cannot emit a fallback.
