"""Strict admission checks for model-produced decisions.

The validator is deliberately pessimistic.  A decision model returns numbers
that look like probabilities, but nothing in this project has measured their
calibration on Chinese counselling turns.  Therefore:

- a decision is only usable if it is well formed, self-consistent, and
  compatible with the deterministic runtime state;
- low confidence is an *abstention*, not a weak vote;
- an illegal or contradictory decision is rejected, never repaired into
  something that would look like a clinical judgement.

The validator never mutates runtime state and never authorises an action.  It
only decides whether the proposal may be forwarded to ``TurnPolicy``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from decision.contracts import (
    ACTION_LABELS,
    DecisionRequest,
    RawDecision,
    clamp_unit,
    is_ending_state,
)

# Reasons are stable strings: tests and the decision journal depend on them.
REASON_PROVIDER_ERROR = "provider_error"
REASON_MISSING_ACTION = "missing_action"
REASON_ILLEGAL_ACTION = "illegal_action"
REASON_MISSING_PROBABILITIES = "missing_probabilities"
REASON_NON_FINITE_PROBABILITY = "non_finite_probability"
REASON_NEGATIVE_PROBABILITY = "negative_probability"
REASON_PROBABILITY_SUM_INVALID = "probability_sum_invalid"
REASON_PROBABILITY_OUT_OF_RANGE = "probability_out_of_range"
REASON_INCONSISTENT_ARGMAX = "inconsistent_argmax"
REASON_BELOW_THRESHOLD = "below_abstain_threshold"
REASON_MISSING_SCALE = "missing_scale_name"
REASON_UNKNOWN_SCALE = "unknown_scale_name"
REASON_ACTIVE_SCALE_CONFLICT = "state_conflict_active_scale"
REASON_SESSION_ENDING_CONFLICT = "state_conflict_session_ending"
REASON_END_SESSION_UNSAFE = "end_requires_deterministic_signal"
REASON_GAME_ALREADY_ACTIVE = "state_conflict_game_active"


def _known_scale_names() -> tuple[str, ...]:
    try:
        from services.scales import SCALES

        return tuple(SCALES.keys())
    except Exception:  # pragma: no cover - protocol-only test environments
        return ("PHQ-9", "GAD-7", "PCL-5")


@dataclass(frozen=True)
class ValidationVerdict:
    """Result of one validation pass."""

    accepted: bool
    decision: RawDecision
    reason: Optional[str] = None
    abstained: bool = False

    @property
    def rejected(self) -> bool:
        return not self.accepted


class DecisionValidator:
    """Admission control between a decision provider and ``TurnPolicy``."""

    def __init__(
        self,
        *,
        abstain_threshold: float = 0.55,
        require_probabilities: bool = True,
        reject_end_session: bool = True,
        probability_tolerance: float = 1e-2,
    ) -> None:
        if not 0.0 < float(abstain_threshold) <= 1.0:
            raise ValueError("abstain_threshold must be in (0, 1]")
        if float(probability_tolerance) <= 0.0:
            raise ValueError("probability_tolerance must be positive")
        self.abstain_threshold = float(abstain_threshold)
        self.require_probabilities = bool(require_probabilities)
        self.reject_end_session = bool(reject_end_session)
        self.probability_tolerance = float(probability_tolerance)

    # -- public API --------------------------------------------------------

    def validate(self, decision: RawDecision, request: DecisionRequest) -> ValidationVerdict:
        if decision.error:
            return self._reject(decision, REASON_PROVIDER_ERROR)

        raw_action = (decision.action or "").strip().lower()
        if not raw_action:
            return self._reject(decision, REASON_MISSING_ACTION)
        if raw_action not in ACTION_LABELS:
            return self._reject(decision, REASON_ILLEGAL_ACTION)

        normalized = decision
        if self.require_probabilities:
            checked = self._check_probabilities(decision, raw_action)
            if isinstance(checked, ValidationVerdict):
                return checked
            normalized = checked

        confidence = self._resolve_confidence(normalized, raw_action)
        normalized = normalized.model_copy(update={"confidence": confidence})

        state_reason = self._state_conflict(raw_action, normalized, request)
        if state_reason:
            return self._reject(normalized, state_reason)

        if confidence + 1e-9 < self.abstain_threshold:
            return ValidationVerdict(
                accepted=False,
                decision=normalized,
                reason=REASON_BELOW_THRESHOLD,
                abstained=True,
            )

        return ValidationVerdict(accepted=True, decision=self._clamped(normalized))

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _reject(decision: RawDecision, reason: str) -> ValidationVerdict:
        return ValidationVerdict(accepted=False, decision=decision, reason=reason)

    def _check_probabilities(
        self, decision: RawDecision, raw_action: str
    ) -> "RawDecision | ValidationVerdict":
        probabilities = dict(decision.probabilities or {})
        if not probabilities:
            return self._reject(decision, REASON_MISSING_PROBABILITIES)

        cleaned: dict[str, float] = {}
        for label, value in probabilities.items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                return self._reject(decision, REASON_NON_FINITE_PROBABILITY)
            if not math.isfinite(number):
                return self._reject(decision, REASON_NON_FINITE_PROBABILITY)
            if number < -self.probability_tolerance:
                return self._reject(decision, REASON_NEGATIVE_PROBABILITY)
            if number > 1.0 + self.probability_tolerance:
                return self._reject(decision, REASON_PROBABILITY_OUT_OF_RANGE)
            cleaned[str(label)] = max(0.0, number)

        total = sum(cleaned.values())
        if not math.isfinite(total) or total <= 0.0:
            return self._reject(decision, REASON_PROBABILITY_SUM_INVALID)
        if abs(total - 1.0) > self.probability_tolerance:
            return self._reject(decision, REASON_PROBABILITY_SUM_INVALID)
        normalized = {label: value / total for label, value in cleaned.items()}

        # The reported answer must actually be the argmax; a provider that
        # picks a label below its own top probability is inconsistent.
        best_label = max(normalized.items(), key=lambda item: item[1])[0]
        if raw_action in normalized and best_label != raw_action:
            return self._reject(decision, REASON_INCONSISTENT_ARGMAX)

        return decision.model_copy(update={"probabilities": normalized})

    @staticmethod
    def _resolve_confidence(decision: RawDecision, raw_action: str) -> float:
        reported = clamp_unit(decision.confidence, default=0.0)
        from_probabilities = decision.probabilities.get(raw_action)
        if from_probabilities is None:
            return reported
        try:
            value = float(from_probabilities)
        except (TypeError, ValueError):
            return reported
        # Trust the distribution over the self-reported scalar: the scalar is
        # free text from a model, the distribution is the readout itself.
        return clamp_unit(value, default=reported)

    def _state_conflict(
        self, raw_action: str, decision: RawDecision, request: DecisionRequest
    ) -> Optional[str]:
        if raw_action == "start_scale":
            if not decision.scale_name:
                return REASON_MISSING_SCALE
            if decision.scale_name not in _known_scale_names():
                return REASON_UNKNOWN_SCALE

        if request.active_scale and raw_action != "chat":
            # An active scale owns the turn; TurnPolicy already enforces this,
            # but rejecting here keeps the reason attributable to the model.
            return REASON_ACTIVE_SCALE_CONFLICT

        if is_ending_state(request.session_state) and raw_action != "chat":
            return REASON_SESSION_ENDING_CONFLICT

        if raw_action == "recommend_game" and request.game_active:
            return REASON_GAME_ALREADY_ACTIVE

        if raw_action == "end_session" and self.reject_end_session:
            # Ending a session requires a deterministic user signal that this
            # layer cannot observe.  A model must never end an interview.
            return REASON_END_SESSION_UNSAFE

        return None

    @staticmethod
    def _clamped(decision: RawDecision) -> RawDecision:
        return decision.model_copy(
            update={
                "confidence": clamp_unit(decision.confidence),
                "intensity": clamp_unit(decision.intensity),
                "reason": str(decision.reason or "")[:160],
            }
        )

    def describe(self) -> dict[str, Any]:
        return {
            "abstain_threshold": self.abstain_threshold,
            "require_probabilities": self.require_probabilities,
            "reject_end_session": self.reject_end_session,
            "probability_tolerance": self.probability_tolerance,
        }
