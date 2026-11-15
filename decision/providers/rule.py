"""Deterministic rule baseline for intervention timing.

This exists to answer one question honestly: *does a decision model beat the
programme's own heuristics?*  Without a rule baseline, "the model proposed an
assessment at round 7" cannot be distinguished from "the counter reached 7".

The rules are deliberately simple and entirely state-driven:

1. unresolved ambiguity          -> clarify
2. safety marker present         -> continue (the safety path owns the turn)
3. not enough interview yet      -> continue
4. enough symptom turns, and a candidate that has not been refused
                                 -> offer a scale
5. anxiety-shaped complaints, late enough, relaxation not done
                                 -> offer relaxation
6. otherwise                     -> continue

It reads only :class:`DecisionRequest`; it never sees the transcript, which
makes it a strong lower bound on "state-only" performance and a useful check
that the model's advantage is not just the state block.
"""

from __future__ import annotations

from typing import Any, Optional

from config import MIN_ROUNDS_BEFORE_SCALE, MIN_ROUNDS_FOR_RELAXATION
from decision.contracts import DecisionRequest, RawDecision
from decision.providers.fake import make_intervention_decision
from decision.timing import InterventionIntent, OfferKind

#: Domains in the deterministic symptom summary that suggest a relaxation
#: exercise could be welcome.  Kept narrow on purpose: this is a baseline, and
#: a blunt rule makes the model's contribution easier to see.
_ANXIETY_HINTS = ("焦虑", "紧张", "烦躁", "心慌", "坐立不安", "压力")


class RuleTimingProvider:
    """State-only heuristic baseline; no model, no network, no randomness."""

    name = "rule_baseline"

    def __init__(
        self,
        *,
        symptom_turns_for_scale: int = 2,
        confidence: float = 0.9,
    ) -> None:
        self.symptom_turns_for_scale = max(1, int(symptom_turns_for_scale))
        self.confidence = float(confidence)

    # -- provider protocol -------------------------------------------------

    def is_available(self) -> bool:
        return True

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "symptom_turns_for_scale": self.symptom_turns_for_scale,
            "min_rounds_before_scale": MIN_ROUNDS_BEFORE_SCALE,
            "min_rounds_for_relaxation": MIN_ROUNDS_FOR_RELAXATION,
        }

    def decide(self, request: DecisionRequest) -> RawDecision:
        intervention, target = self._choose(request)
        return make_intervention_decision(
            intervention.value,
            offer_target=target,
            confidence=self.confidence,
            provider=self.name,
            reason="rule_baseline",
        )

    # -- rules -------------------------------------------------------------

    def _choose(self, request: DecisionRequest) -> tuple[InterventionIntent, Optional[str]]:
        if request.risk_marker:
            # A rule engine must not trade a safety situation for an activity
            # either; the risk path decides what happens next.
            return InterventionIntent.CONTINUE_INTERVIEW, None

        if request.clarification_pending:
            return InterventionIntent.CLARIFY_FIRST, None

        # An unanswered invitation owns the turn; do not stack another.
        if request.offer_state.pending is not None:
            return InterventionIntent.CONTINUE_INTERVIEW, None

        scale = self._scale_choice(request)
        if scale is not None:
            return InterventionIntent.OFFER_SCALE, scale

        if self._relaxation_is_timely(request):
            return InterventionIntent.OFFER_RELAXATION, None

        return InterventionIntent.CONTINUE_INTERVIEW, None

    def _scale_choice(self, request: DecisionRequest) -> Optional[str]:
        if request.active_scale or request.current_round < MIN_ROUNDS_BEFORE_SCALE:
            return None
        if request.symptom_turns < self.symptom_turns_for_scale:
            return None
        candidates = request.scale_candidates()
        if not candidates:
            return None
        if request.offer_state.cooldown_active(
            OfferKind.SCALE, request.current_round, cooldown=4
        ):
            return None
        # Highest-priority candidate: resume an incomplete instrument first.
        return candidates[0].name

    def _relaxation_is_timely(self, request: DecisionRequest) -> bool:
        if request.relaxation_done or request.relaxation_used or request.relaxation_active:
            return False
        if request.current_round < MIN_ROUNDS_FOR_RELAXATION:
            return False
        if request.offer_state.relaxation_offered():
            return False
        if request.offer_state.cooldown_active(
            OfferKind.RELAXATION, request.current_round, cooldown=4
        ):
            return False
        summary = request.symptom_summary or ""
        return any(hint in summary for hint in _ANXIETY_HINTS)
