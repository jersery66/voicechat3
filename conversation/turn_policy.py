"""Pure, deterministic authority for one conversation turn."""

from __future__ import annotations

from config import (
    MIN_ROUNDS_BEFORE_SCALE,
    MIN_ROUNDS_FOR_RELAXATION,
    RELAX_ROUTE_CONFIDENCE,
    SCALE_ROUTE_CONFIDENCE,
)
from conversation.contracts import (
    RouterAction,
    RouterProposal,
    TurnAction,
    TurnDecision,
    TurnSignals,
    TurnStateSnapshot,
)


_ENDING_STATES = {"SESSION_ENDING", "SESSION_ENDED"}
_RELAXATION_TYPE_ALIASES = {
    "breathing": "breathing",
    "呼吸": "breathing",
    "muscle": "muscle",
    "muscle_relaxation": "muscle",
    "肌肉": "muscle",
    "meditation": "meditation",
    "mindfulness": "meditation",
    "冥想": "meditation",
}


def _relaxation_type(value: str | None) -> str | None:
    """Canonicalize only an explicit user content preference.

    A missing value means "open the relaxation flow"; it must never silently
    become breathing merely because a Router or deterministic candidate did
    not name a specific content item.
    """
    if value is None or not str(value).strip():
        return None
    return _RELAXATION_TYPE_ALIASES.get(str(value).strip().lower())


class TurnPolicy:
    """Decide exactly once from immutable proposal, snapshot, and signals.

    No model, I/O, UI, or mutable runtime object is touched here.  The
    pipeline remains the temporary production caller during Phase 2; it passes
    the resulting immutable decision to the execution path.
    """

    def decide(
        self,
        *,
        user_text: str,
        proposal: RouterProposal,
        snapshot: TurnStateSnapshot,
        signals: TurnSignals,
    ) -> TurnDecision:
        del user_text  # Kept in the signature for deterministic auditability.

        # Priority 1: explicit user ending always wins.
        if signals.explicit_end_requested:
            return TurnDecision(
                action=TurnAction.END_SESSION,
                end_reason="user_explicit",
                confidence=1.0,
                reason="explicit_end",
            )

        # An explicit user request for an intervention is a signal that may
        # be approved immediately.  It is intentionally checked before the
        # active-scale continuation branch so a user can ask to pause a
        # questionnaire without the Router taking control of the item.
        if signals.explicit_relaxation_requested:
            return TurnDecision(
                action=TurnAction.RECOMMEND_RELAXATION,
                intervention_type=_relaxation_type(signals.explicit_relaxation_type),
                confidence=1.0,
                reason="user_relaxation_request",
            )

        # Critical transcript ambiguity is an observation, not an action. The
        # policy owns the decision to ask for clarification. Active-scale
        # turns remain CONTINUE_SCALE so ScaleAnswerInterpreter can clarify
        # the current Runtime-owned item without bypassing the scale flow.
        if signals.semantic_ambiguity and not snapshot.active_scale:
            return TurnDecision(
                action=TurnAction.CLARIFY_INPUT,
                semantic_target=signals.semantic_target,
                confidence=1.0,
                reason=signals.semantic_reason or "critical_input_ambiguity",
                needs_rag=False,
            )

        # Priority 2: an active scale owns an answer/refusal turn.  This also
        # prevents a Router proposal from switching to another scale.
        if snapshot.active_scale:
            if signals.active_scale_pause_requested or signals.active_scale_refusal:
                return TurnDecision(
                    action=TurnAction.PAUSE_SCALE,
                    scale_name=snapshot.active_scale,
                    confidence=1.0,
                    reason="active_scale_pause",
                    needs_rag=False,
                )
            return TurnDecision(
                action=TurnAction.CONTINUE_SCALE,
                scale_name=snapshot.active_scale,
                confidence=1.0,
                reason="active_scale_waiting" if snapshot.waiting_for_answer else "active_scale",
                needs_rag=False,
            )

        # Priority 3: no new work can be started once the lifecycle is ending.
        if snapshot.session_state in _ENDING_STATES:
            return TurnDecision(
                action=TurnAction.END_SESSION,
                end_reason=snapshot.session_state.lower(),
                confidence=1.0,
                reason="session_ending",
            )
        if snapshot.time_limit_reached:
            # The SessionEngine owns the one-shot timeout event and the UI
            # asks the user to continue or end.  A turn arriving before that
            # choice is consumed must not silently become END_SESSION.
            return self._chat("time_limit_pending_choice")

        # Priority 4: deterministic scale observations are approved here, not
        # at their detection site.  A Router start proposal follows the same
        # gates and never controls an item number.
        requested_scale = proposal.scale_name if proposal.action is RouterAction.START_SCALE else None
        router_eligible = bool(
            requested_scale
            and snapshot.round_count >= MIN_ROUNDS_BEFORE_SCALE
            and proposal.confidence >= SCALE_ROUTE_CONFIDENCE
            and requested_scale not in snapshot.completed_scales
        )
        deterministic_scale = signals.deterministic_scale_candidate
        deterministic_eligible = bool(
            deterministic_scale
            and snapshot.round_count >= MIN_ROUNDS_BEFORE_SCALE
            and deterministic_scale not in snapshot.completed_scales
        )
        if requested_scale and snapshot.round_count < MIN_ROUNDS_BEFORE_SCALE:
            return self._chat("router_before_min_rounds")
        if requested_scale and proposal.confidence < SCALE_ROUTE_CONFIDENCE and not deterministic_eligible:
            return self._chat("router_below_confidence")
        if requested_scale and requested_scale in snapshot.completed_scales and not deterministic_eligible:
            return self._chat("router_scale_completed")

        if router_eligible and deterministic_eligible:
            if requested_scale != deterministic_scale:
                return TurnDecision(
                    action=TurnAction.CHAT,
                    needs_rag=False,
                    confidence=1.0,
                    reason="scale_candidate_conflict",
                )
            return TurnDecision(
                action=TurnAction.START_SCALE,
                scale_name=requested_scale,
                confidence=proposal.confidence,
                reason="router_deterministic_agreement",
                needs_rag=False,
            )
        if router_eligible:
            return TurnDecision(
                action=TurnAction.START_SCALE,
                scale_name=requested_scale,
                confidence=proposal.confidence,
                reason="router_start_scale_accepted",
                needs_rag=False,
            )
        if deterministic_eligible:
            return TurnDecision(
                action=TurnAction.START_SCALE,
                scale_name=deterministic_scale,
                confidence=1.0,
                reason="deterministic_scale_signal",
                needs_rag=False,
            )
        if proposal.action is RouterAction.START_SCALE:
            return self._chat("router_invalid_scale")

        # A clear user game request is higher priority than a queued
        # proactive relaxation candidate.  Entertainment context alone never
        # reaches this branch because the signal is explicit-request only.
        if signals.explicit_game_requested and not snapshot.game_active:
            return TurnDecision(
                action=TurnAction.RECOMMEND_GAME,
                confidence=1.0,
                reason="user_game_request",
            )

        proactive_relaxation = (
            proposal.action is RouterAction.RECOMMEND_RELAXATION
            or bool(signals.proactive_relaxation_candidate)
        )
        if proactive_relaxation:
            if snapshot.relaxation_used or snapshot.proactive_relaxation_offered:
                return self._chat("proactive_relaxation_already_offered")
            if snapshot.round_count < MIN_ROUNDS_FOR_RELAXATION:
                return self._chat("proactive_relaxation_before_min_rounds")
            if proposal.action is RouterAction.RECOMMEND_RELAXATION and proposal.confidence < RELAX_ROUTE_CONFIDENCE:
                return self._chat("router_below_confidence")
            return TurnDecision(
                action=TurnAction.RECOMMEND_RELAXATION,
                intervention_type=None,
                confidence=proposal.confidence if proposal.action is RouterAction.RECOMMEND_RELAXATION else 1.0,
                reason="proactive_relaxation_accepted",
            )

        if proposal.action is RouterAction.RECOMMEND_GAME:
            if snapshot.game_active:
                return self._chat("router_game_rejected")
            if not signals.explicit_game_requested:
                return self._chat("game_requires_explicit_request")
            return TurnDecision(
                action=TurnAction.RECOMMEND_GAME,
                intervention_type=None,
                confidence=proposal.confidence,
                reason="router_game_accepted",
            )

        # END_SESSION from the Router is still only a proposal and cannot end
        # a session without a deterministic explicit-end signal.
        if proposal.action is RouterAction.END_SESSION:
            return self._chat("router_end_rejected")

        return self._chat("router_fallback" if proposal.reason == "router_fallback" else "default_chat", proposal)

    @staticmethod
    def _chat(reason: str, proposal: RouterProposal | None = None) -> TurnDecision:
        proposal = proposal or RouterProposal.fallback(reason)
        return TurnDecision(
            action=TurnAction.CHAT,
            needs_rag=proposal.needs_rag,
            confidence=proposal.confidence,
            reason=reason,
        )
