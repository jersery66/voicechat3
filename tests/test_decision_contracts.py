"""Contract tests for the decision layer: request, raw decision, projection."""

from __future__ import annotations

import pytest

from conversation.agent_observation import AgentObservation
from conversation.contracts import RouterAction, RouterProposal
from decision.contracts import (
    ACTION_LABELS,
    EMOTION_LABELS,
    INTENT_LABELS,
    LEGACY_INTENTS,
    DecisionRequest,
    RawDecision,
    build_observation,
    map_emotion_to_legacy,
    map_intent_to_legacy,
)


def _request(**overrides):
    payload = {"user_text": "我最近睡不好", "current_round": 4}
    payload.update(overrides)
    return DecisionRequest(**payload)


def test_action_vocabulary_matches_router_vocabulary():
    assert set(ACTION_LABELS) == {action.value for action in RouterAction}
    assert len(ACTION_LABELS) == 5


def test_intent_vocabulary_is_stable_and_has_unclear():
    assert len(INTENT_LABELS) == len(set(INTENT_LABELS))
    assert "unclear" in INTENT_LABELS
    assert "uncertain" in EMOTION_LABELS


@pytest.mark.parametrize("intent", INTENT_LABELS)
def test_every_intent_maps_into_the_legacy_vocabulary(intent):
    mapped = map_intent_to_legacy(intent)
    assert mapped in LEGACY_INTENTS


@pytest.mark.parametrize(
    "emotion,expected",
    [
        ("calm", "calm"),
        ("sad", "sad"),
        ("anxious", "anxious"),
        ("angry", "angry"),
        ("irritable", "angry"),
        ("positive", "happy"),
        ("uncertain", "neutral"),
    ],
)
def test_emotion_projection(emotion, expected):
    assert map_emotion_to_legacy(emotion) == expected


def test_request_is_frozen():
    request = _request()
    with pytest.raises(Exception):
        request.user_text = "改掉"  # type: ignore[misc]


def test_request_legacy_kwargs_keep_the_historical_shape():
    request = _request(active_scale="PHQ-9", collected_scales={"PHQ-9": {1: 2}})
    kwargs = request.legacy_kwargs()
    assert set(kwargs) == {
        "user_text",
        "recent_history",
        "current_round",
        "active_scale",
        "collected_scales",
        "relaxation_done",
    }
    assert kwargs["collected_scales"] == {"PHQ-9": {1: 2}}


def test_request_redaction_never_exposes_text():
    request = _request(user_text="张三住在幸福路，想结束生命", recent_history="以前聊过家庭")
    redacted = request.redacted()
    flattened = " ".join(str(value) for value in redacted.values())
    assert "张三" not in flattened
    assert "幸福路" not in flattened
    assert "结束生命" not in flattened
    assert redacted["text_len"] == len(request.user_text)


def test_build_observation_projects_a_well_formed_decision():
    decision = RawDecision(
        provider="fake",
        action="recommend_relaxation",
        emotion="anxious",
        intensity=0.7,
        confidence=0.82,
        reason="tension",
        intent_jev="help_seeking",
        probabilities={"recommend_relaxation": 0.82, "chat": 0.18},
    )
    observation = build_observation(decision, source="jev")
    assert isinstance(observation, AgentObservation)
    assert observation.proposal.action is RouterAction.RECOMMEND_RELAXATION
    assert observation.proposal.emotion == "anxious"
    assert observation.intent == "counseling"
    assert observation.source == "jev"
    assert observation.fallback_used is False


def test_build_observation_degrades_an_unknown_action_to_chat():
    decision = RawDecision(provider="fake", action="diagnose_depression", confidence=0.99)
    observation = build_observation(decision, source="jev")
    assert observation.proposal.action is RouterAction.CHAT
    assert observation.fallback_used is True
    assert "unknown_action" in observation.proposal.reason


def test_build_observation_degrades_start_scale_without_a_scale():
    decision = RawDecision(provider="fake", action="start_scale", confidence=0.99)
    observation = build_observation(decision, source="jev")
    assert observation.proposal.action is RouterAction.CHAT
    assert observation.fallback_used is True


def test_build_observation_keeps_a_registered_scale():
    decision = RawDecision(
        provider="fake", action="start_scale", scale_name="PHQ-9", confidence=0.9
    )
    observation = build_observation(decision, source="jev")
    assert observation.proposal.action is RouterAction.START_SCALE
    assert observation.proposal.scale_name == "PHQ-9"


def test_build_observation_rejects_an_unregistered_scale_without_raising():
    decision = RawDecision(
        provider="fake", action="start_scale", scale_name="BDI-II", confidence=0.9
    )
    observation = build_observation(decision, source="jev")
    assert observation.proposal.action is RouterAction.CHAT
    assert observation.fallback_used is True


def test_build_observation_clamps_out_of_range_numbers():
    decision = RawDecision(
        provider="fake", action="chat", intensity=9.5, confidence=-3.0
    )
    observation = build_observation(decision, source="jev")
    assert 0.0 <= observation.proposal.intensity <= 1.0
    assert observation.proposal.confidence == 0.0


def test_legacy_observation_is_returned_by_identity():
    supplied = AgentObservation(
        proposal=RouterProposal(action=RouterAction.CHAT, confidence=0.33),
        intent="chitchat",
        source="agent",
    )
    decision = RawDecision(provider="legacy_qwen", observation=supplied)
    assert build_observation(decision, source="jev") is supplied
