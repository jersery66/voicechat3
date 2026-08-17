"""Correction B2: explicit relaxation type is deterministic and user-owned."""

from __future__ import annotations

from conversation.contracts import RouterProposal, TurnAction, TurnSignals, TurnStateSnapshot
from conversation.turn_policy import TurnPolicy
from conversation.turn_signals import collect_turn_signals, detect_explicit_relaxation_request


def test_typed_relaxation_requests_are_canonicalized():
    assert detect_explicit_relaxation_request("我想做呼吸练习") == (True, "breathing")
    assert detect_explicit_relaxation_request("我想做肌肉放松") == (True, "muscle")
    assert detect_explicit_relaxation_request("我想做冥想") == (True, "meditation")
    assert detect_explicit_relaxation_request("我想做正念练习") == (True, "meditation")


def test_generic_relaxation_request_has_no_invented_type():
    assert detect_explicit_relaxation_request("我想放松一下") == (True, None)


def test_historical_relaxation_mentions_are_not_requests():
    for text in ("以前做过冥想", "老师教过我冥想", "冥想对我没用"):
        assert detect_explicit_relaxation_request(text) == (False, None)


def test_user_explicit_type_overrides_agent_proposal():
    signals = collect_turn_signals("我想做冥想", TurnStateSnapshot(round_count=1))
    decision = TurnPolicy().decide(
        user_text="我想做冥想",
        proposal=RouterProposal(intervention_type="breathing"),
        snapshot=TurnStateSnapshot(round_count=1),
        signals=signals,
    )
    assert decision.action is TurnAction.RECOMMEND_RELAXATION
    assert decision.intervention_type == "meditation"


def test_explicit_type_survives_agent_unavailable_signal_path():
    signals = collect_turn_signals("我想做肌肉放松", TurnStateSnapshot(round_count=0))
    decision = TurnPolicy().decide(
        user_text="我想做肌肉放松",
        proposal=RouterProposal.fallback("agent unavailable"),
        snapshot=TurnStateSnapshot(round_count=0),
        signals=signals,
    )
    assert decision.action is TurnAction.RECOMMEND_RELAXATION
    assert decision.intervention_type == "muscle"


def test_active_scale_pauses_only_after_policy_decision():
    signals = collect_turn_signals(
        "我先不答了，我想做个冥想",
        TurnStateSnapshot(active_scale="PHQ-9", current_item=1, waiting_for_answer=True),
    )
    assert signals.explicit_relaxation_type == "meditation"
    decision = TurnPolicy().decide(
        user_text="我先不答了，我想做个冥想",
        proposal=RouterProposal.fallback("agent unavailable"),
        snapshot=TurnStateSnapshot(active_scale="PHQ-9", current_item=1, waiting_for_answer=True),
        signals=signals,
    )
    assert decision.action is TurnAction.RECOMMEND_RELAXATION
    assert decision.intervention_type == "meditation"
