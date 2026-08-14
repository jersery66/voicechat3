"""Pure, deterministic authority for one conversation turn."""

from __future__ import annotations

from config import MIN_ROUNDS_BEFORE_SCALE, RELAX_ROUTE_CONFIDENCE, SCALE_ROUTE_CONFIDENCE
from conversation.contracts import (
    RouterAction,
    RouterProposal,
    TurnAction,
    TurnDecision,
    TurnSignals,
    TurnStateSnapshot,
)


_ENDING_STATES = {"SESSION_ENDING", "SESSION_ENDED"}


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
            return TurnDecision(
                action=TurnAction.END_SESSION,
                end_reason="time_limit",
                confidence=1.0,
                reason="time_limit",
            )

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

        if proposal.action is RouterAction.RECOMMEND_RELAXATION:
            if snapshot.relaxation_used:
                return self._chat("router_relaxation_rejected")
            if proposal.confidence < RELAX_ROUTE_CONFIDENCE:
                return self._chat("router_below_confidence")
            return TurnDecision(
                action=TurnAction.RECOMMEND_RELAXATION,
                intervention_type=proposal.intervention_type,
                confidence=proposal.confidence,
                reason="router_relaxation_accepted",
            )

        if proposal.action is RouterAction.RECOMMEND_GAME:
            if snapshot.game_active:
                return self._chat("router_game_rejected")
            return TurnDecision(
                action=TurnAction.RECOMMEND_GAME,
                intervention_type=proposal.intervention_type,
                confidence=proposal.confidence,
                reason="router_game_accepted",
            )

        # END_SESSION from the Router is still only a proposal; it is accepted
        # here (lower priority than explicit text) so the policy, not the 3B,
        # remains the sole authority.
        if proposal.action is RouterAction.END_SESSION:
            return TurnDecision(
                action=TurnAction.END_SESSION,
                end_reason="router_proposal",
                confidence=proposal.confidence,
                reason="router_end_accepted",
            )

        if signals.legacy_relaxation_candidate and not snapshot.relaxation_used:
            return TurnDecision(
                action=TurnAction.RECOMMEND_RELAXATION,
                intervention_type=signals.legacy_relaxation_candidate,
                confidence=1.0,
                reason="legacy_relaxation_signal",
            )
        if signals.legacy_game_candidate and not snapshot.game_active:
            return TurnDecision(
                action=TurnAction.RECOMMEND_GAME,
                confidence=1.0,
                reason="legacy_game_signal",
            )

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
