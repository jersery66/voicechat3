"""Contract and policy tests for the Phase 2 turn-authority boundary."""

import pytest

from conversation.contracts import (
    RouterAction,
    RouterProposal,
    TurnAction,
    TurnDecision,
    TurnStateSnapshot,
)
from conversation.turn_policy import TurnPolicy
from conversation.turn_signals import TurnSignals


def snapshot(**overrides):
    values = {
        "session_state": "CHATTING",
        "round_count": 8,
        "active_scale": None,
        "current_item": None,
        "waiting_for_answer": False,
        "completed_scales": (),
        "relaxation_used": False,
        "game_active": False,
        "time_limit_reached": False,
    }
    values.update(overrides)
    return TurnStateSnapshot(**values)


def proposal(action=RouterAction.CHAT, **overrides):
    values = {"action": action, "confidence": 0.9, "reason": "test"}
    values.update(overrides)
    return RouterProposal(**values)


def test_router_proposal_has_no_item_or_score_fields_and_is_immutable():
    fields = set(RouterProposal.model_fields)
    assert {"scale_item", "requested_item", "scale_score", "target_item"}.isdisjoint(fields)
    p = proposal(RouterAction.START_SCALE, scale_name="PHQ-9")
    with pytest.raises((TypeError, ValueError)):
        p.action = RouterAction.CHAT


def test_start_scale_requires_a_registered_scale():
    with pytest.raises(ValueError):
        proposal(RouterAction.START_SCALE, scale_name="UNKNOWN")


def test_turn_state_snapshot_is_immutable_and_not_a_second_state_source():
    state = snapshot(active_scale="PHQ-9", current_item=3, waiting_for_answer=True)
    with pytest.raises((TypeError, ValueError)):
        state.active_scale = "GAD-7"


def test_explicit_end_wins_over_every_router_proposal():
    decision = TurnPolicy().decide(
        user_text="不想聊了，退出",
        proposal=proposal(RouterAction.START_SCALE, scale_name="PHQ-9"),
        snapshot=snapshot(active_scale="PHQ-9", current_item=3, waiting_for_answer=True),
        signals=TurnSignals(explicit_end_requested=True),
    )
    assert decision.action is TurnAction.END_SESSION
    assert decision.reason == "explicit_end"
    assert decision.end_reason == "user_explicit"


@pytest.mark.parametrize("action", [
    RouterAction.START_SCALE,
    RouterAction.RECOMMEND_RELAXATION,
    RouterAction.RECOMMEND_GAME,
])
def test_waiting_scale_wins_over_non_end_router_proposals(action):
    decision = TurnPolicy().decide(
        user_text="继续",
        proposal=proposal(action, scale_name="GAD-7" if action is RouterAction.START_SCALE else None),
        snapshot=snapshot(active_scale="PHQ-9", current_item=3, waiting_for_answer=True),
        signals=TurnSignals(),
    )
    assert decision.action is TurnAction.CONTINUE_SCALE
    assert decision.scale_name == "PHQ-9"
    assert decision.reason == "active_scale_waiting"


def test_scale_refusal_pauses_before_router_can_switch_scale():
    decision = TurnPolicy().decide(
        user_text="我现在不想继续答这个了",
        proposal=proposal(RouterAction.START_SCALE, scale_name="GAD-7"),
        snapshot=snapshot(active_scale="PHQ-9", current_item=3, waiting_for_answer=True),
        signals=TurnSignals(active_scale_refusal=True),
    )
    assert decision.action is TurnAction.PAUSE_SCALE
    assert decision.scale_name == "PHQ-9"
    assert decision.reason == "active_scale_pause"


def test_router_start_scale_is_gated_by_round_and_confidence():
    low_round = TurnPolicy().decide(
        user_text="最近睡不好",
        proposal=proposal(RouterAction.START_SCALE, scale_name="PHQ-9"),
        snapshot=snapshot(round_count=4),
        signals=TurnSignals(),
    )
    low_confidence = TurnPolicy().decide(
        user_text="最近睡不好",
        proposal=proposal(RouterAction.START_SCALE, scale_name="PHQ-9", confidence=0.1),
        snapshot=snapshot(round_count=8),
        signals=TurnSignals(),
    )
    assert low_round.action is TurnAction.CHAT
    assert low_round.reason == "router_before_min_rounds"
    assert low_confidence.action is TurnAction.CHAT
    assert low_confidence.reason == "router_below_confidence"


def test_deterministic_scale_signal_is_approved_by_policy_not_executed_directly():
    decision = TurnPolicy().decide(
        user_text="最近睡不好",
        proposal=proposal(RouterAction.CHAT, confidence=0.0),
        snapshot=snapshot(round_count=8),
        signals=TurnSignals(deterministic_scale_candidate="PHQ-9"),
    )
    assert decision.action is TurnAction.START_SCALE
    assert decision.scale_name == "PHQ-9"
    assert decision.reason == "deterministic_scale_signal"


def test_completed_scale_cannot_be_started_again():
    decision = TurnPolicy().decide(
        user_text="再做一次",
        proposal=proposal(RouterAction.START_SCALE, scale_name="PHQ-9"),
        snapshot=snapshot(completed_scales=("PHQ-9",)),
        signals=TurnSignals(),
    )
    assert decision.action is TurnAction.CHAT
    assert decision.reason == "router_scale_completed"


def test_router_timeout_fallback_is_normal_chat():
    decision = TurnPolicy().decide(
        user_text="你好",
        proposal=RouterProposal.fallback("router_fallback"),
        snapshot=snapshot(round_count=1),
        signals=TurnSignals(),
    )
    assert decision.action is TurnAction.CHAT
    assert decision.confidence == 0.0
    assert decision.reason == "router_fallback"


def test_decision_action_specific_validation():
    with pytest.raises(ValueError):
        TurnDecision(action=TurnAction.START_SCALE)
    with pytest.raises(ValueError):
        TurnDecision(action=TurnAction.END_SESSION)
    assert TurnDecision(action=TurnAction.CHAT).scale_name is None
