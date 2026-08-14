"""Phase 5 policy tests: intervention, game, explicit end, and timeout signals."""

import pytest

from conversation.contracts import (
    RouterAction,
    RouterProposal,
    TurnAction,
    TurnSignals,
    TurnStateSnapshot,
)
from conversation.turn_policy import TurnPolicy
from conversation.turn_signals import collect_turn_signals
from core.scoring import is_user_explicit_end_text
from core.session_fsm import SessionState
from core.types import EndType
from app.contracts import EndSessionCommand, StartSessionCommand, SubjectInfo
from app.engine import SessionEngine


def snapshot(**overrides):
    values = {
        "session_state": "CHATTING",
        "round_count": 8,
        "active_scale": None,
        "current_item": None,
        "waiting_for_answer": False,
        "completed_scales": (),
        "relaxation_used": False,
        "proactive_relaxation_offered": False,
        "game_active": False,
        "time_limit_reached": False,
    }
    values.update(overrides)
    return TurnStateSnapshot(**values)


def proposal(action=RouterAction.CHAT, **overrides):
    values = {"action": action, "confidence": 0.95, "reason": "test"}
    values.update(overrides)
    return RouterProposal(**values)


def decide(*, text="", action=RouterAction.CHAT, state=None, signals=None, **proposal_overrides):
    state = state or snapshot()
    signals = signals or TurnSignals()
    return TurnPolicy().decide(
        user_text=text,
        proposal=proposal(action, **proposal_overrides),
        snapshot=state,
        signals=signals,
    )


def test_explicit_relaxation_request_is_allowed_before_minimum_rounds():
    decision = decide(
        text="我想做个放松练习",
        state=snapshot(round_count=1),
        signals=TurnSignals(explicit_relaxation_requested=True),
        intervention_type="breathing",
    )
    assert decision.action is TurnAction.RECOMMEND_RELAXATION
    assert decision.reason == "user_relaxation_request"


def test_relaxation_decision_cannot_carry_game_media_type():
    decision = decide(
        text="鎴戞兂鍋氫釜鏀炬澗缁冧範",
        signals=TurnSignals(explicit_relaxation_requested=True),
        intervention_type="game",
    )
    assert decision.action is TurnAction.RECOMMEND_RELAXATION
    assert decision.intervention_type == "breathing"


def test_proactive_relaxation_before_threshold_is_rejected():
    decision = decide(
        text="最近有点累",
        state=snapshot(round_count=7),
        action=RouterAction.RECOMMEND_RELAXATION,
        intervention_type="breathing",
    )
    assert decision.action is TurnAction.CHAT
    assert decision.reason == "proactive_relaxation_before_min_rounds"


def test_proactive_relaxation_after_threshold_is_approved_once():
    decision = decide(
        text="最近有点累",
        state=snapshot(round_count=8),
        action=RouterAction.RECOMMEND_RELAXATION,
        intervention_type="breathing",
    )
    assert decision.action is TurnAction.RECOMMEND_RELAXATION
    assert decision.reason == "proactive_relaxation_accepted"


def test_proactive_relaxation_is_rejected_after_session_allowance_is_used():
    decision = decide(
        state=snapshot(proactive_relaxation_offered=True),
        action=RouterAction.RECOMMEND_RELAXATION,
        intervention_type="breathing",
    )
    assert decision.action is TurnAction.CHAT
    assert decision.reason == "proactive_relaxation_already_offered"


def test_waiting_scale_blocks_proactive_relaxation_but_user_request_can_pause():
    waiting = snapshot(
        active_scale="PHQ-9",
        current_item=3,
        waiting_for_answer=True,
        round_count=10,
    )
    proactive = decide(
        state=waiting,
        action=RouterAction.RECOMMEND_RELAXATION,
        intervention_type="breathing",
    )
    assert proactive.action is TurnAction.CONTINUE_SCALE
    assert proactive.scale_name == "PHQ-9"

    requested = decide(
        text="我想先做个放松",
        state=waiting,
        signals=TurnSignals(explicit_relaxation_requested=True),
        intervention_type="breathing",
    )
    assert requested.action is TurnAction.RECOMMEND_RELAXATION
    assert requested.reason == "user_relaxation_request"


def test_game_requires_explicit_user_request():
    explicit = decide(
        text="我想玩个游戏",
        action=RouterAction.RECOMMEND_GAME,
        signals=TurnSignals(explicit_game_requested=True),
    )
    assert explicit.action is TurnAction.RECOMMEND_GAME

    boredom = decide(
        text="我好无聊",
        action=RouterAction.RECOMMEND_GAME,
    )
    assert boredom.action is TurnAction.CHAT
    assert boredom.reason == "game_requires_explicit_request"


def test_explicit_game_request_precedes_a_pending_proactive_candidate():
    decision = decide(
        text="鎴戞兂鐜╀釜娓告垙",
        signals=TurnSignals(
            explicit_game_requested=True,
            proactive_relaxation_candidate="breathing",
        ),
    )
    assert decision.action is TurnAction.RECOMMEND_GAME
    assert decision.reason == "user_game_request"


def test_router_end_proposal_without_explicit_user_signal_is_rejected():
    decision = decide(action=RouterAction.END_SESSION)
    assert decision.action is TurnAction.CHAT
    assert decision.reason == "router_end_rejected"


def test_time_limit_signal_opens_choice_but_does_not_authorize_end():
    decision = decide(state=snapshot(time_limit_reached=True))
    assert decision.action is TurnAction.CHAT
    assert decision.reason == "time_limit_pending_choice"


@pytest.mark.parametrize("text", ["好多了", "轻松了", "舒服点了", "我感觉好多了"])
def test_positive_feedback_is_not_explicit_end(text):
    signals = collect_turn_signals(text, snapshot())
    assert signals.explicit_end_requested is False
    decision = decide(text=text, signals=signals)
    assert decision.action is TurnAction.CHAT


@pytest.mark.parametrize("text", ["结束", "不想聊了", "退出", "今天先这样"])
def test_explicit_end_phrases_are_authoritative(text):
    assert is_user_explicit_end_text(text) is True
    signals = collect_turn_signals(text, snapshot())
    decision = decide(text=text, action=RouterAction.RECOMMEND_GAME, signals=signals)
    assert decision.action is TurnAction.END_SESSION
    assert decision.end_reason == "user_explicit"


def test_signal_collection_distinguishes_explicit_requests_from_boredom():
    relax = collect_turn_signals("我想放松一下", snapshot(round_count=1))
    game = collect_turn_signals("我想玩游戏", snapshot())
    boredom = collect_turn_signals("我好无聊", snapshot())
    assert relax.explicit_relaxation_requested is True
    assert game.explicit_game_requested is True
    assert boredom.explicit_game_requested is False


def test_explicit_end_command_is_not_forced_through_relaxation():
    events = []
    engine = SessionEngine(emit=events.append)
    try:
        engine.process_command(StartSessionCommand(subject=SubjectInfo(subject_id="P5")))
        engine.process_command(EndSessionCommand(
            end_type=EndType.GOAL_ACHIEVED,
            allow_force_relaxation=False,
        ))
        assert engine.state is SessionState.SESSION_ENDING
        assert not any(getattr(event, "kind", "") == "relaxation_recommended" for event in events)
    finally:
        engine.shutdown()
