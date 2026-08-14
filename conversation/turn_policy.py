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


def _relaxation_type(value: str | None) -> str:
    return _RELAXATION_TYPE_ALIASES.get(str(value or "").strip().lower(), "breathing")


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
                intervention_type=_relaxation_type(proposal.intervention_type),
                confidence=1.0,
                reason="user_relaxation_request",
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
                needs_rag=True,
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
        candidate_scale = requested_scale or signals.deterministic_scale_candidate
        if candidate_scale:
            if snapshot.round_count < MIN_ROUNDS_BEFORE_SCALE:
                return self._chat("router_before_min_rounds")
            if requested_scale and proposal.confidence < SCALE_ROUTE_CONFIDENCE:
                return self._chat("router_below_confidence")
            if candidate_scale in snapshot.completed_scales:
                return self._chat("router_scale_completed")
            return TurnDecision(
                action=TurnAction.START_SCALE,
                scale_name=candidate_scale,
                confidence=proposal.confidence if requested_scale else 1.0,
                reason=("router_start_scale_accepted" if requested_scale else "deterministic_scale_signal"),
                needs_rag=True,
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
                intervention_type=_relaxation_type(
                    proposal.intervention_type
                    or signals.proactive_relaxation_candidate
                ),
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
                intervention_type=proposal.intervention_type,
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
