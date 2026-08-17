"""Correction B3: explicit Router/deterministic scale resolution."""

from __future__ import annotations

from conversation.contracts import RouterAction, RouterProposal, TurnAction, TurnSignals, TurnStateSnapshot
from conversation.turn_policy import TurnPolicy


def _decision(proposal, deterministic=None, *, rounds=5, completed=(), active=None):
    return TurnPolicy().decide(
        user_text="合成输入",
        proposal=proposal,
        snapshot=TurnStateSnapshot(
            round_count=rounds,
            completed_scales=tuple(completed),
            active_scale=active,
            current_item=1 if active else None,
            waiting_for_answer=bool(active),
        ),
        signals=TurnSignals(deterministic_scale_candidate=deterministic),
    )


def test_router_and_deterministic_agreement_starts_once():
    decision = _decision(
        RouterProposal(action=RouterAction.START_SCALE, scale_name="PHQ-9", confidence=0.9),
        "PHQ-9",
    )
    assert decision.action is TurnAction.START_SCALE
    assert decision.scale_name == "PHQ-9"
    assert decision.reason == "router_deterministic_agreement"


def test_eligible_disagreement_becomes_chat_without_rag():
    decision = _decision(
        RouterProposal(action=RouterAction.START_SCALE, scale_name="PHQ-9", confidence=0.9),
        "GAD-7",
    )
    assert decision.action is TurnAction.CHAT
    assert decision.reason == "scale_candidate_conflict"
    assert decision.needs_rag is False


def test_low_confidence_router_does_not_conflict_with_deterministic_candidate():
    decision = _decision(
        RouterProposal(action=RouterAction.START_SCALE, scale_name="PHQ-9", confidence=0.4),
        "GAD-7",
    )
    assert decision.action is TurnAction.START_SCALE
    assert decision.scale_name == "GAD-7"
    assert decision.reason == "deterministic_scale_signal"


def test_completed_router_candidate_does_not_conflict_with_new_deterministic_candidate():
    decision = _decision(
        RouterProposal(action=RouterAction.START_SCALE, scale_name="PHQ-9", confidence=0.9),
        "GAD-7",
        completed=("PHQ-9",),
    )
    assert decision.action is TurnAction.START_SCALE
    assert decision.scale_name == "GAD-7"


def test_router_only_and_deterministic_only_candidates_start():
    router_only = _decision(
        RouterProposal(action=RouterAction.START_SCALE, scale_name="PHQ-9", confidence=0.9),
    )
    deterministic_only = _decision(RouterProposal(action=RouterAction.CHAT), "GAD-7")
    assert router_only.action is TurnAction.START_SCALE
    assert router_only.scale_name == "PHQ-9"
    assert deterministic_only.action is TurnAction.START_SCALE
    assert deterministic_only.scale_name == "GAD-7"


def test_both_candidates_before_minimum_round_are_chat():
    decision = _decision(
        RouterProposal(action=RouterAction.START_SCALE, scale_name="PHQ-9", confidence=0.9),
        "GAD-7",
        rounds=4,
    )
    assert decision.action is TurnAction.CHAT


def test_active_scale_continuation_precedes_new_candidates():
    decision = _decision(
        RouterProposal(action=RouterAction.START_SCALE, scale_name="GAD-7", confidence=0.9),
        "GAD-7",
        active="PHQ-9",
    )
    assert decision.action is TurnAction.CONTINUE_SCALE
    assert decision.scale_name == "PHQ-9"


def test_conflict_does_not_create_scale_runtime_state():
    decision = _decision(
        RouterProposal(action=RouterAction.START_SCALE, scale_name="PHQ-9", confidence=0.9),
        "GAD-7",
    )
    assert decision.action is TurnAction.CHAT
    assert decision.scale_name is None
