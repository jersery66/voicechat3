"""Pure, deterministic authority for one conversation turn.

The policy approves or refuses; it never proposes.  Two additions on this
branch matter for interview timing:

- **Invitations are a separate stage.**  ``OFFER_SCALE`` / ``OFFER_RELAXATION``
  ask the participant a question and change no assessment state.  Only an
  answer (or an explicit user request) can reach ``START_SCALE`` /
  ``RECOMMEND_RELAXATION``.
- **Refusals are remembered.**  A declined offer enters a cooldown, so the same
  questionnaire is not proposed again a turn later.

Every gate here is a floor, not a trigger.  ``MIN_ROUNDS_BEFORE_SCALE`` and
``MIN_ROUNDS_FOR_RELAXATION`` say "not before", never "once you reach this
round, start something".
"""

from __future__ import annotations

from config import (
    MIN_ROUNDS_BEFORE_SCALE,
    MIN_ROUNDS_FOR_RELAXATION,
    OFFER_RELAXATION_COOLDOWN_ROUNDS,
    OFFER_SCALE_COOLDOWN_ROUNDS,
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
from decision.timing import (
    OFFER_REASON_ANSWER_AMBIGUOUS,
    OFFER_REASON_CLARIFYING,
    OFFER_REASON_DEFERRED,
    OFFER_REASON_RELAXATION_ACCEPTED,
    OFFER_REASON_RELAXATION_DECLINED,
    OFFER_REASON_SCALE_ACCEPTED,
    OFFER_REASON_SCALE_DECLINED,
    OFFER_REASON_TARGET_MISSING,
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

        # Priority 0: a safety signal owns the turn.
        #
        # The risk path is a separate flow with its own escalation.  Trading it
        # for a questionnaire or an exercise is the one failure this layer must
        # make impossible, including when the participant also asks to stop.
        # The decision here only refuses to act; it never concludes "no risk".
        if signals.risk_marker or snapshot.risk_marker or snapshot.risk_in_session:
            return self._chat("offer_blocked_risk")

        # Priority 1: explicit user ending always wins over ordinary work.
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

        # Priority 2: an outstanding invitation owns the answer.
        # This sits above every proposal so a Router cannot talk over a
        # question the participant is already being asked.
        offer_decision = self._resolve_pending_offer(snapshot, signals)
        if offer_decision is not None:
            return offer_decision

        # An explicit request for an assessment is the participant's own
        # initiative.  Asking "would you like to?" back at someone who just
        # asked would be obtuse, so the policy starts the next planned
        # instrument directly -- but only from the registered candidate list.
        #
        # The minimum-round floor deliberately does not apply here: it exists to
        # stop the *system* from proposing an assessment too early, not to
        # refuse a participant who asks for one.
        if signals.explicit_scale_requested and not snapshot.active_scale:
            if not snapshot.scale_candidates:
                return self._chat("user_scale_request_no_candidate")
            return TurnDecision(
                action=TurnAction.START_SCALE,
                scale_name=snapshot.scale_candidates[0],
                confidence=1.0,
                reason="user_scale_request",
                needs_rag=False,
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

        # A decision backend may also observe that the turn needs clarifying.
        # It is an observation like the one above, and equally gated.
        if proposal.clarify and not snapshot.active_scale:
            return TurnDecision(
                action=TurnAction.CLARIFY_INPUT,
                semantic_target=signals.semantic_target,
                confidence=proposal.confidence,
                reason="router_clarify_first",
                needs_rag=False,
            )

        # Priority 3: an active scale owns an answer/refusal turn.  This also
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

        # Priority 4: no new work can be started once the lifecycle is ending.
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

        # Priority 5: invitations.  Checked before any executable scale or
        # relaxation branch, because a proposal that is only an invitation must
        # never be turned into an execution.
        if proposal.action is RouterAction.START_SCALE and proposal.offer == "scale":
            return self._scale_offer(proposal, snapshot, signals)
        if proposal.action is RouterAction.RECOMMEND_RELAXATION and proposal.offer == "relaxation":
            return self._relaxation_offer(proposal, snapshot, signals)

        # Priority 6: deterministic scale observations are approved here, not
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
            if signals.risk_marker:
                return self._chat("proactive_relaxation_blocked_risk")
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

    # -- invitation handling ----------------------------------------------

    def _resolve_pending_offer(
        self, snapshot: TurnStateSnapshot, signals: TurnSignals
    ) -> TurnDecision | None:
        """Turn an answer to an outstanding invitation into a decision.

        Returns ``None`` when there is nothing to resolve, so the turn continues
        through the ordinary path.  ``START_SCALE`` is only reachable from an
        accepted scale invitation or an explicit user request -- never from a
        model suggestion alone.
        """
        pending = snapshot.pending_offer
        if not pending or snapshot.active_scale:
            return None

        if signals.offer_accepted:
            if pending == "scale":
                target = snapshot.pending_offer_target
                if not target:
                    return self._chat(OFFER_REASON_TARGET_MISSING)
                if snapshot.scale_candidates and target not in snapshot.scale_candidates:
                    return self._chat(OFFER_REASON_TARGET_MISSING)
                if target in snapshot.completed_scales:
                    return self._chat("offer_scale_completed")
                if snapshot.round_count < MIN_ROUNDS_BEFORE_SCALE:
                    return self._chat("offer_scale_before_min_rounds")
                return TurnDecision(
                    action=TurnAction.START_SCALE,
                    scale_name=target,
                    confidence=1.0,
                    reason=OFFER_REASON_SCALE_ACCEPTED,
                    needs_rag=False,
                )
            return TurnDecision(
                action=TurnAction.RECOMMEND_RELAXATION,
                intervention_type=_relaxation_type(signals.explicit_relaxation_type),
                confidence=1.0,
                reason=OFFER_REASON_RELAXATION_ACCEPTED,
            )

        if signals.offer_declined:
            reason = (
                OFFER_REASON_SCALE_DECLINED
                if pending == "scale"
                else OFFER_REASON_RELAXATION_DECLINED
            )
            return self._chat(reason)

        if signals.offer_question:
            # "What would that involve?" keeps the invitation open.  The reply
            # must explain, not start anything and not ask them to repeat.
            return self._chat(OFFER_REASON_CLARIFYING)

        if signals.offer_deferred:
            # "Not now, later" is not a refusal; the invitation survives and may
            # later expire only if the conversation actually moves on.
            return self._chat(OFFER_REASON_DEFERRED)

        if signals.offer_answer_ambiguous:
            # Ask what they meant instead of assuming either answer.
            return TurnDecision(
                action=TurnAction.CLARIFY_INPUT,
                semantic_target="offer_answer",
                confidence=1.0,
                reason=OFFER_REASON_ANSWER_AMBIGUOUS,
                needs_rag=False,
            )
        return None

    def _scale_offer(
        self,
        proposal: RouterProposal,
        snapshot: TurnStateSnapshot,
        signals: TurnSignals,
    ) -> TurnDecision:
        """Approve asking about a questionnaire.  Never starts one."""
        scale = proposal.scale_name
        if not scale:
            return self._chat("offer_scale_without_target")
        if snapshot.round_count < MIN_ROUNDS_BEFORE_SCALE:
            # The programme floor is a hard gate, not a schedule.
            return self._chat("offer_scale_before_min_rounds")
        if proposal.confidence < SCALE_ROUTE_CONFIDENCE:
            return self._chat("router_below_confidence")
        if scale in snapshot.completed_scales:
            return self._chat("offer_scale_completed")
        if snapshot.scale_candidates and scale not in snapshot.scale_candidates:
            return self._chat("offer_scale_not_a_candidate")
        if scale in snapshot.declined_scales:
            return self._chat("offer_scale_declined_before")
        if snapshot.pending_offer is not None:
            return self._chat("offer_already_pending")
        if self._scale_cooldown_active(snapshot):
            return self._chat("offer_scale_cooldown")
        if signals.risk_marker or snapshot.risk_marker or snapshot.risk_in_session:
            # A safety situation is not an assessment opportunity; the risk
            # path owns this turn.
            return self._chat("offer_blocked_risk")
        if signals.participant_wants_to_talk:
            # They just asked for the floor.  An invitation is an interruption.
            return self._chat("offer_blocked_wants_to_talk")
        return TurnDecision(
            action=TurnAction.OFFER_SCALE,
            scale_name=scale,
            confidence=proposal.confidence,
            reason="offer_scale_proposed",
            needs_rag=False,
        )

    def _relaxation_offer(
        self,
        proposal: RouterProposal,
        snapshot: TurnStateSnapshot,
        signals: TurnSignals,
    ) -> TurnDecision:
        """Approve inviting a relaxation exercise.  Never starts one."""
        if snapshot.round_count < MIN_ROUNDS_FOR_RELAXATION:
            return self._chat("offer_relaxation_before_min_rounds")
        if proposal.confidence < RELAX_ROUTE_CONFIDENCE:
            return self._chat("router_below_confidence")
        if snapshot.relaxation_used or snapshot.proactive_relaxation_offered:
            return self._chat("offer_relaxation_already_done")
        if snapshot.pending_offer is not None:
            return self._chat("offer_already_pending")
        if self._relaxation_cooldown_active(snapshot):
            return self._chat("offer_relaxation_cooldown")
        if signals.risk_marker or snapshot.risk_marker:
            return self._chat("offer_blocked_risk")
        if signals.participant_wants_to_talk:
            return self._chat("offer_blocked_wants_to_talk")
        return TurnDecision(
            action=TurnAction.OFFER_RELAXATION,
            intervention_type=None,
            confidence=proposal.confidence,
            reason="offer_relaxation_proposed",
            needs_rag=False,
        )

    @staticmethod
    def _scale_cooldown_active(snapshot: TurnStateSnapshot) -> bool:
        declined_at = snapshot.last_scale_decline_round
        if declined_at is None or OFFER_SCALE_COOLDOWN_ROUNDS <= 0:
            return False
        return (snapshot.round_count - declined_at) < OFFER_SCALE_COOLDOWN_ROUNDS

    @staticmethod
    def _relaxation_cooldown_active(snapshot: TurnStateSnapshot) -> bool:
        declined_at = snapshot.last_relaxation_decline_round
        if declined_at is None or OFFER_RELAXATION_COOLDOWN_ROUNDS <= 0:
            return False
        return (snapshot.round_count - declined_at) < OFFER_RELAXATION_COOLDOWN_ROUNDS

    @staticmethod
    def _chat(reason: str, proposal: RouterProposal | None = None) -> TurnDecision:
        proposal = proposal or RouterProposal.fallback(reason)
        return TurnDecision(
            action=TurnAction.CHAT,
            needs_rag=proposal.needs_rag,
            confidence=proposal.confidence,
            reason=reason,
        )
