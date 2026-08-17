# Correction B audit: Agent observation, relaxation type, and scale conflicts

Baseline: Correction A.1 HEAD
`316637778257e43609a70fd79b9776e93d1256c9`.

## B1 current call graph

On an ordinary `ConversationPipeline.execute` turn, the production path first
calls `AgentService.route_proposal()` (which currently adapts one
`route_conversation_actions()` structured request). After language generation,
the pipeline submits `_classify_intent_emotion()`, which separately calls
`classify_intent()` and `detect_emotion()`; those methods may themselves use
keyword fast paths or a model request. Thus the ordinary routed path can make
one route request plus two independent observation calls.

`RouterProposal.action`, scale, intervention, confidence, and `needs_rag` feed
TurnPolicy. Its emotion/intensity are currently not the authoritative action
inputs. `result.intent` is used for reporting/entertainment labeling and
`AgentService.recommend_scene()` utilities; `result.emotion_result` feeds the
emotion tracker and report/UI-facing observation data. Neither is allowed to
write ScaleRuntime or SessionEngine state.

## B2 current relaxation signal

`TurnSignals` currently contains only `explicit_relaxation_requested: bool`.
The deterministic phrase list recognizes typed phrases but discards the type;
TurnPolicy then normalizes proposal/default intervention type. Historical
statement mentions are not distinguished from request-like phrases.

## B3 current scale candidate resolution

Pipeline computes a deterministic candidate from symptom observations and
passes it with the Router proposal. TurnPolicy currently resolves
`proposal.scale_name or deterministic_scale_candidate`, so two eligible,
different candidates silently prefer the Router proposal. Active-scale
continuation occurs before candidate resolution and must remain unchanged.

Correction B will add only an immutable observation contract, explicit
relaxation type facts, and deterministic candidate conflict resolution. It
will not modify SessionEngine, ScaleRuntime, PreDeliveryGuard, providers,
profiles, prompts, or Correction A semantics.

## B.1 closure audit

Correction B.1 keeps the same authority boundaries while closing two fallback
edges.  An internal `route_conversation_actions()` failure now projects both
keyword intent and keyword emotion/intensity into the same deterministic,
non-executable `CHAT` observation used when the Agent is unavailable.  No
secondary model request is issued.

Relaxation detection now requires a short request marker immediately before a
typed or generic technique phrase, with a small explicit set of standalone
commands.  Historical, evaluative, and completed-practice mentions remain
passive observations and cannot become `RECOMMEND_RELAXATION` signals.

The deterministic scale candidate remains bounded by construction: pipeline
symptom candidates are selected from the registered scale manager keys, and
the optional hard trigger delegates to the same internal registered-scale
detector.  No external/untrusted candidate source is used by production
pipeline wiring; no additional validation layer was added in B.1.
