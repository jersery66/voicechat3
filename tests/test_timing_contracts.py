"""Contracts for the intervention-timing layer.

These tests pin the vocabulary and the *separation of stages*: an invitation is
not an execution, a refusal is remembered, and no scale name can exist that the
runtime could not actually start.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from conversation.contracts import RouterProposal, TurnAction, TurnDecision
from decision.contracts import DecisionRequest, RawDecision, build_observation
from decision.timing import (
    INTERVENTION_OPTION_TEXTS,
    SCALE_CHOICE_NONE,
    InterventionIntent,
    OfferKind,
    OfferOutcome,
    OfferPolicy,
    OfferRecord,
    OfferState,
    intervention_question,
    intervention_to_router_action,
    normalize_intervention,
    offer_kind_for,
    rank_scale_candidates,
    scale_choice_option_texts,
)
from conversation.offer_ledger import OfferLedger


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def test_four_interventions_are_the_whole_vocabulary():
    assert [intent.value for intent in InterventionIntent] == [
        "continue_interview",
        "offer_scale",
        "offer_relaxation",
        "clarify_first",
    ]


def test_offer_is_not_execution_in_the_router_projection():
    assert intervention_to_router_action("offer_scale") == "start_scale"
    assert intervention_to_router_action("offer_relaxation") == "recommend_relaxation"
    assert intervention_to_router_action("continue_interview") == "chat"
    assert intervention_to_router_action("clarify_first") == "chat"
    # The kind is what distinguishes an invitation from an execution.
    assert offer_kind_for("offer_scale") is OfferKind.SCALE
    assert offer_kind_for("offer_relaxation") is OfferKind.RELAXATION
    assert offer_kind_for("continue_interview") is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Continue interviewing", "continue_interview"),
        ("Offer a scale assessment", "offer_scale"),
        ("Offer a relaxation exercise", "offer_relaxation"),
        ("Clarify before proceeding", "clarify_first"),
        ("offer_scale", "offer_scale"),
        ("  OFFER_SCALE  ", "offer_scale"),
    ],
)
def test_normalize_intervention_accepts_option_text_and_label(value, expected):
    assert normalize_intervention(value) == expected


def test_normalize_intervention_rejects_unknown_text():
    assert normalize_intervention("start the scale now") is None
    assert normalize_intervention(None) is None


def test_option_texts_cover_every_intent():
    assert len(INTERVENTION_OPTION_TEXTS) == len(InterventionIntent)
    for text in INTERVENTION_OPTION_TEXTS:
        assert normalize_intervention(text) is not None
    assert len(intervention_question("zh")) > 0


# ---------------------------------------------------------------------------
# Scale candidates: only registered instruments, resume before fresh
# ---------------------------------------------------------------------------


def test_candidate_ranking_prefers_resuming_an_incomplete_scale():
    ranked = rank_scale_candidates(
        registered=("PHQ-9", "GAD-7", "PCL-5"),
        completed=(),
        administered=("GAD-7",),
    )
    assert [candidate.name for candidate in ranked] == ["GAD-7", "PHQ-9", "PCL-5"]
    assert ranked[0].reason == "incomplete"


def test_completed_and_declined_scales_are_not_candidates():
    ranked = rank_scale_candidates(
        registered=("PHQ-9", "GAD-7", "PCL-5"),
        completed=("PHQ-9",),
        declined=("GAD-7",),
    )
    assert [candidate.name for candidate in ranked] == ["PCL-5"]


def test_declined_scales_can_be_readmitted_for_observation_only():
    blocked = rank_scale_candidates(registered=("PHQ-9", "GAD-7"), declined=("PHQ-9",))
    assert [candidate.name for candidate in blocked] == ["GAD-7"]

    readmitted = rank_scale_candidates(
        registered=("PHQ-9", "GAD-7"), declined=("PHQ-9",), declined_blocks=False
    )
    assert [candidate.name for candidate in readmitted] == ["PHQ-9", "GAD-7"]


def test_scale_choice_options_always_include_a_hold_off_option():
    ranked = rank_scale_candidates(registered=("PHQ-9",))
    options = scale_choice_option_texts(ranked)
    assert options[-1] == SCALE_CHOICE_NONE
    assert "PHQ-9" in options


# ---------------------------------------------------------------------------
# Offer state
# ---------------------------------------------------------------------------


def _record(kind: OfferKind, *, target=None, made=1, status=OfferOutcome.PENDING, resolved=None):
    return OfferRecord(
        kind=kind,
        target=target,
        made_at_round=made,
        last_touched_round=resolved if resolved is not None else made,
        status=status,
        resolved_at_round=resolved,
    )


def test_pending_offer_is_visible_and_decline_round_is_remembered():
    state = OfferState(
        records=(
            _record(
                OfferKind.SCALE,
                target="PHQ-9",
                made=5,
                status=OfferOutcome.DECLINED,
                resolved=6,
            ),
        )
    )
    assert state.pending is None
    assert state.declined_scales() == ("PHQ-9",)
    assert state.declined_round(OfferKind.SCALE) == 6


def test_cooldown_is_active_right_after_a_refusal_and_expires():
    state = OfferState(
        records=(
            _record(
                OfferKind.RELAXATION,
                made=8,
                status=OfferOutcome.DECLINED,
                resolved=9,
            ),
        )
    )
    assert state.cooldown_active(OfferKind.RELAXATION, 10, 4) is True
    assert state.cooldown_active(OfferKind.RELAXATION, 13, 4) is False
    assert state.cooldown_active(OfferKind.RELAXATION, 10, 0) is False


def test_ledger_cancels_an_unanswered_invitation_when_a_newer_one_is_made():
    ledger = OfferLedger()
    ledger.record_offer(OfferKind.SCALE, target="PHQ-9", round_count=5)
    ledger.record_offer(OfferKind.SCALE, target="GAD-7", round_count=6)
    state = ledger.state()
    assert state.pending is not None
    assert state.pending.target == "GAD-7"
    cancelled = [r for r in state.records if r.status is OfferOutcome.CANCELLED]
    assert len(cancelled) == 1
    assert cancelled[0].resolution_note == "superseded_by_new_offer"


def test_ledger_expires_only_after_sustained_topic_drift():
    """A turn count alone must never close an invitation.

    The participant may answer three turns later; only consecutive turns that
    neither answer nor mention the invitation may expire it.
    """
    policy = OfferPolicy(topic_drift_grace_rounds=3)
    ledger = OfferLedger(policy)
    ledger.record_offer(OfferKind.SCALE, target="PHQ-9", round_count=5)
    assert ledger.expire_on_topic_drift(6, engaged=False) == []
    assert ledger.expire_on_topic_drift(7, engaged=False) == []
    # Referring to the invitation resets the drift clock.
    assert ledger.expire_on_topic_drift(8, engaged=True) == []
    assert ledger.expire_on_topic_drift(9, engaged=False) == []
    assert ledger.expire_on_topic_drift(10, engaged=False) == []
    expired = ledger.expire_on_topic_drift(11, engaged=False)
    assert [record.status for record in expired] == [OfferOutcome.EXPIRED]
    assert expired[0].resolution_note == "topic_drift"
    assert ledger.state().pending is None


def test_ledger_resolution_only_closes_the_open_invitation():
    ledger = OfferLedger()
    ledger.record_offer(OfferKind.SCALE, target="PHQ-9", round_count=5)
    resolved = ledger.resolve_pending(OfferOutcome.ACCEPTED, 6)
    assert resolved is not None and resolved.status is OfferOutcome.ACCEPTED
    assert ledger.resolve_pending(OfferOutcome.DECLINED, 7) is None


# ---------------------------------------------------------------------------
# Observation mapping
# ---------------------------------------------------------------------------


def _raw(intervention, **kwargs):
    return RawDecision(provider="test", intervention=intervention, **kwargs)


def test_offer_scale_with_a_target_becomes_an_invitation_not_a_start():
    observation = build_observation(
        _raw("offer_scale", offer_target="GAD-7", confidence=0.9), source="jev"
    )
    proposal = observation.proposal
    assert proposal.action.value == "start_scale"
    assert proposal.offer == "scale"
    assert proposal.scale_name == "GAD-7"


def test_offer_scale_without_a_target_degrades_to_chat_and_says_why():
    observation = build_observation(_raw("offer_scale", confidence=0.9), source="jev")
    proposal = observation.proposal
    assert proposal.action.value == "chat"
    assert proposal.offer is None
    assert observation.fallback_used is True
    assert "missing_scale_name" in observation.proposal.reason


def test_offer_relaxation_carries_the_offer_kind():
    observation = build_observation(_raw("offer_relaxation", confidence=0.9), source="jev")
    assert observation.proposal.action.value == "recommend_relaxation"
    assert observation.proposal.offer == "relaxation"


def test_clarify_first_sets_the_clarify_flag():
    observation = build_observation(_raw("clarify_first", confidence=0.9), source="jev")
    assert observation.proposal.clarify is True


def test_router_proposal_rejects_unknown_offer_kind():
    with pytest.raises(ValidationError):
        RouterProposal(action="chat", confidence=0.5, reason="x", offer="game")


def test_offer_scale_decision_requires_a_registered_scale_name():
    with pytest.raises(ValidationError):
        TurnDecision(action=TurnAction.OFFER_SCALE, confidence=1.0, reason="test")


def test_offer_decisions_cannot_carry_answer_fields():
    with pytest.raises(ValidationError):
        TurnDecision(
            action=TurnAction.OFFER_SCALE,
            scale_name="PHQ-9",
            answered_item=2,
            confidence=1.0,
            reason="test",
        )


def test_offer_relaxation_cannot_carry_a_scale():
    with pytest.raises(ValidationError):
        TurnDecision(
            action=TurnAction.OFFER_RELAXATION,
            scale_name="PHQ-9",
            confidence=1.0,
            reason="test",
        )


# ---------------------------------------------------------------------------
# Decision request state
# ---------------------------------------------------------------------------


def test_request_scale_candidates_come_from_the_registry():
    request = DecisionRequest(
        user_text="我最近睡不好",
        current_round=6,
        registered_scales=("PHQ-9", "GAD-7"),
        administered_scales=("GAD-7",),
    )
    assert [candidate.name for candidate in request.scale_candidates()] == ["GAD-7", "PHQ-9"]


def test_request_summary_mentions_phase_and_prior_offers():
    request = DecisionRequest(
        user_text="我最近睡不好",
        current_round=6,
        registered_scales=("PHQ-9",),
        symptom_summary="睡眠方面的主诉",
        offer_state=OfferState(
            records=(
                _record(
                    OfferKind.SCALE,
                    target="PHQ-9",
                    made=5,
                    status=OfferOutcome.DECLINED,
                    resolved=6,
                ),
            )
        ),
    )
    assert request.phase() in ("opening", "exploration", "assessment")
    # The state block the model reads must state the refusal explicitly,
    # otherwise the model cannot know it should not ask again.
    assert any("PHQ-9" in part for part in request.offer_state.summary_parts())
    assert "拒绝" in "".join(request.offer_state.summary_parts())
