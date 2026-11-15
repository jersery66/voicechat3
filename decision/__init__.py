"""Pluggable decision layer for the voicechat3 conversation agent.

The layer only *produces observations*.  ``conversation.turn_policy`` remains
the single execution authority, and ``assessment.scale_runtime`` remains the
only owner of scale state.  Nothing here can mutate a session, a score, or a
clinical conclusion.

Public entry points:

- ``DecisionCoordinator`` — runs one of the three modes (legacy/shadow/jev).
- ``DecisionRequest`` — the immutable input of one decision.
- ``DecisionValidator`` — rejects illegal, malformed, or unstable outputs.
- ``build_decision_coordinator`` — wires providers from ``RuntimeConfig``.
"""

from decision.contracts import (
    ACTION_LABELS,
    CLARIFY_LABELS,
    EMOTION_LABELS,
    INTENT_LABELS,
    LEGACY_INTENTS,
    DecisionBackend,
    DecisionMode,
    DecisionOutcome,
    DecisionProvider,
    DecisionRequest,
    EscalationMode,
    RawDecision,
    build_observation,
)
from decision.coordinator import DecisionCoordinator
from decision.validator import DecisionValidator, ValidationVerdict

__all__ = [
    "ACTION_LABELS",
    "CLARIFY_LABELS",
    "EMOTION_LABELS",
    "INTENT_LABELS",
    "LEGACY_INTENTS",
    "DecisionBackend",
    "DecisionCoordinator",
    "DecisionMode",
    "DecisionOutcome",
    "DecisionProvider",
    "DecisionRequest",
    "DecisionValidator",
    "EscalationMode",
    "RawDecision",
    "ValidationVerdict",
    "build_observation",
]
