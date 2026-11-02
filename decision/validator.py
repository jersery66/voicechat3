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

Two families of checks live here:

``intervention`` decisions (four-way timing: continue / offer scale / offer
relaxation / clarify) are checked against the offer history and the registered
scale candidates.  This is where "suggest a scale that was already refused two
turns ago" dies, and where "offer a scale that does not exist" dies.

``action`` decisions are the legacy five-way router vocabulary, kept unchanged
so ``legacy`` mode behaves exactly as it did before this branch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from config import OFFER_RELAXATION_COOLDOWN_ROUNDS, OFFER_SCALE_COOLDOWN_ROUNDS
from decision.contracts import (
    ACTION_LABELS,
    DecisionRequest,
    RawDecision,
    clamp_unit,
    is_ending_state,
)
from decision.timing import (
    INTERVENTION_LABELS,
    OfferKind,
    intervention_to_router_action,
    normalize_intervention,
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

# -- timing-specific rejections ---------------------------------------------
REASON_UNKNOWN_INTERVENTION = "unknown_intervention"
REASON_NO_SCALE_CANDIDATE = "no_scale_candidate"
REASON_SCALE_NOT_A_CANDIDATE = "scale_not_a_candidate"
REASON_SCALE_ALREADY_COMPLETED = "scale_already_completed"
REASON_SCALE_ALREADY_DECLINED = "scale_already_declined"
REASON_SCALE_OFFER_COOLDOWN = "scale_offer_cooldown"
REASON_RELAXATION_ALREADY_DONE = "relaxation_already_done"
REASON_RELAXATION_OFFER_COOLDOWN = "relaxation_offer_cooldown"
REASON_OFFER_ALREADY_PENDING = "offer_already_pending"
REASON_RISK_REQUIRES_SAFETY = "risk_requires_safety_path"


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
        scale_cooldown_rounds: int = OFFER_SCALE_COOLDOWN_ROUNDS,
        relaxation_cooldown_rounds: int = OFFER_RELAXATION_COOLDOWN_ROUNDS,
    ) -> None:
        if not 0.0 < float(abstain_threshold) <= 1.0:
            raise ValueError("abstain_threshold must be in (0, 1]")
        if float(probability_tolerance) <= 0.0:
            raise ValueError("probability_tolerance must be positive")
        self.abstain_threshold = float(abstain_threshold)
        self.require_probabilities = bool(require_probabilities)
        self.reject_end_session = bool(reject_end_session)
        self.probability_tolerance = float(probability_tolerance)
        self.scale_cooldown_rounds = int(scale_cooldown_rounds)
        self.relaxation_cooldown_rounds = int(relaxation_cooldown_rounds)

    # -- public API --------------------------------------------------------

    def validate(self, decision: RawDecision, request: DecisionRequest) -> ValidationVerdict:
        if decision.error:
            return self._reject(decision, REASON_PROVIDER_ERROR)

        intervention = decision.normalized_intervention()
        if decision.intervention and intervention is None:
            # A provider that answers outside the four-way vocabulary is not
            # answering the timing question at all.
            return self._reject(decision, REASON_UNKNOWN_INTERVENTION)
        if intervention is not None:
            return self._validate_intervention(decision, request, intervention)
        return self._validate_action(decision, request)

    # -- intervention path -------------------------------------------------

    def _validate_intervention(
        self, decision: RawDecision, request: DecisionRequest, intervention: str
    ) -> ValidationVerdict:
        normalized = decision
        if self.require_probabilities:
            checked = self._check_intervention_probabilities(decision, intervention)
            if isinstance(checked, ValidationVerdict):
                return checked
            normalized = checked

        action = intervention_to_router_action(intervention)
        normalized = normalized.model_copy(
            update={
                "action": action,
                "probabilities": dict(normalized.probabilities),
            }
        )

        confidence = self._resolve_confidence(normalized, intervention)
        normalized = normalized.model_copy(update={"confidence": confidence})

        state_reason = self._intervention_conflict(intervention, normalized, request)
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

    def _check_intervention_probabilities(
        self, decision: RawDecision, intervention: str
    ) -> "RawDecision | ValidationVerdict":
        probabilities = dict(decision.probabilities or {})
        if not probabilities:
            return self._reject(decision, REASON_MISSING_PROBABILITIES)

        cleaned: dict[str, float] = {}
        for label, value in probabilities.items():
            normalized_label = normalize_intervention(label)
            if normalized_label is None:
                return self._reject(decision, REASON_UNKNOWN_INTERVENTION)
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
            cleaned[normalized_label] = cleaned.get(normalized_label, 0.0) + max(0.0, number)

        total = sum(cleaned.values())
        if not math.isfinite(total) or total <= 0.0:
            return self._reject(decision, REASON_PROBABILITY_SUM_INVALID)
        if abs(total - 1.0) > self.probability_tolerance:
            return self._reject(decision, REASON_PROBABILITY_SUM_INVALID)
        normalized = {label: value / total for label, value in cleaned.items()}

        if intervention not in normalized:
            # The answer is not in its own distribution; nothing to resolve.
            return self._reject(decision, REASON_MISSING_PROBABILITIES)
        best_label = max(normalized.items(), key=lambda item: item[1])[0]
        if best_label != intervention:
            return self._reject(decision, REASON_INCONSISTENT_ARGMAX)

        return decision.model_copy(update={"probabilities": normalized})

    def _intervention_conflict(
        self, intervention: str, decision: RawDecision, request: DecisionRequest
    ) -> Optional[str]:
        if is_ending_state(request.session_state):
            if intervention != "continue_interview":
                return REASON_SESSION_ENDING_CONFLICT
        if request.active_scale and intervention in ("offer_scale", "offer_relaxation"):
            # A questionnaire in progress owns the turn.
            return REASON_ACTIVE_SCALE_CONFLICT

        if intervention == "offer_scale":
            return self._scale_offer_conflict(decision, request)
        if intervention == "offer_relaxation":
            return self._relaxation_offer_conflict(request)
        return None

    def _scale_offer_conflict(
        self, decision: RawDecision, request: DecisionRequest
    ) -> Optional[str]:
        target = (decision.offer_target or decision.scale_name or "").strip()
        if not target:
            return REASON_MISSING_SCALE
        if target not in _known_scale_names():
            return REASON_UNKNOWN_SCALE

        candidates = {candidate.name for candidate in request.scale_candidates()}
        if not candidates:
            # Nothing registered, nothing incomplete: there is no scale to
            # offer, so a scale offer is a fabrication.
            return REASON_NO_SCALE_CANDIDATE
        if target not in candidates:
            if target in request.completed_scales:
                return REASON_SCALE_ALREADY_COMPLETED
            if target in request.offer_state.declined_scales():
                return REASON_SCALE_ALREADY_DECLINED
            return REASON_SCALE_NOT_A_CANDIDATE

        if self._offer_already_pending(request):
            return REASON_OFFER_ALREADY_PENDING

        declined_at = request.offer_state.declined_round(OfferKind.SCALE)
        if declined_at is not None and self.scale_cooldown_rounds > 0:
            if (request.current_round - declined_at) < self.scale_cooldown_rounds:
                return REASON_SCALE_OFFER_COOLDOWN

        if request.risk_marker:
            return REASON_RISK_REQUIRES_SAFETY
        return None

    def _relaxation_offer_conflict(self, request: DecisionRequest) -> Optional[str]:
        if request.relaxation_done or request.relaxation_used:
            return REASON_RELAXATION_ALREADY_DONE
        if self._offer_already_pending(request):
            return REASON_OFFER_ALREADY_PENDING

        declined_at = request.offer_state.declined_round(OfferKind.RELAXATION)
        if declined_at is not None and self.relaxation_cooldown_rounds > 0:
            if (request.current_round - declined_at) < self.relaxation_cooldown_rounds:
                return REASON_RELAXATION_OFFER_COOLDOWN

        if request.risk_marker:
            return REASON_RISK_REQUIRES_SAFETY
        return None

    @staticmethod
    def _offer_already_pending(request: DecisionRequest) -> bool:
        return request.offer_state.pending is not None

    # -- legacy action path ------------------------------------------------

    def _validate_action(
        self, decision: RawDecision, request: DecisionRequest
    ) -> ValidationVerdict:
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
    def _resolve_confidence(decision: RawDecision, label: str) -> float:
        reported = clamp_unit(decision.confidence, default=0.0)
        from_probabilities = decision.probabilities.get(label)
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
            "scale_cooldown_rounds": self.scale_cooldown_rounds,
            "relaxation_cooldown_rounds": self.relaxation_cooldown_rounds,
            "intervention_labels": list(INTERVENTION_LABELS),
        }
