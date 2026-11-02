"""Mode behaviour: legacy pass-through, shadow isolation, jev fallback.

The tests here are the ones that would catch a regression in the safety story:
a model must not be able to end an interview, start a scale, or mutate state.
"""

from __future__ import annotations

import json
import types

import pytest

from conversation.agent_observation import AgentObservation
from conversation.contracts import RouterAction, RouterProposal, TurnStateSnapshot, TurnSignals
from conversation.turn_policy import TurnPolicy
from decision.contracts import (
    DecisionMode,
    DecisionRequest,
    EscalationMode,
    RawDecision,
)
from decision.coordinator import DecisionCoordinator
from decision.journal import DecisionJournal, DecisionJournalError
from decision.providers.fake import FakeDecisionProvider, make_decision
from decision.providers.legacy import LegacyDecisionProvider
from decision.validator import DecisionValidator


def _request(**overrides):
    payload = {"user_text": "我最近总是睡不好", "current_round": 6}
    payload.update(overrides)
    return DecisionRequest(**payload)


class RecordingAgent:
    """Legacy agent double that counts calls."""

    model = "qwen-agent-double"

    def __init__(self, action="chat", confidence=0.8):
        self.calls = 0
        self._action = action
        self._confidence = confidence

    def is_available(self):
        return True

    def observe_turn(self, **_kwargs):
        self.calls += 1
        return AgentObservation(
            proposal=RouterProposal(
                action=RouterAction(self._action), confidence=self._confidence
            ),
            intent="counseling",
        )


def _coordinator(
    mode,
    primary,
    *,
    legacy=None,
    escalation=EscalationMode.LEGACY,
    journal=None,
    threshold=0.55,
    shadow=None,
):
    return DecisionCoordinator(
        mode=mode,
        primary=primary,
        validator=DecisionValidator(abstain_threshold=threshold),
        shadow=shadow,
        legacy=legacy,
        escalation=escalation,
        journal=journal,
    )


# ---------------------------------------------------------------------------
# legacy
# ---------------------------------------------------------------------------


def test_legacy_mode_returns_the_observation_unchanged():
    agent = RecordingAgent(action="recommend_relaxation", confidence=0.66)
    coordinator = _coordinator(
        DecisionMode.LEGACY, LegacyDecisionProvider(agent), legacy=LegacyDecisionProvider(agent)
    )
    outcome = coordinator.observe_turn(_request())
    assert outcome.mode is DecisionMode.LEGACY
    assert outcome.provider == "legacy_qwen"
    assert outcome.observation.proposal.action is RouterAction.RECOMMEND_RELAXATION
    assert outcome.escalated is False
    assert agent.calls == 1


def test_legacy_mode_does_not_validate_or_rewrite():
    agent = RecordingAgent(action="end_session", confidence=0.99)
    coordinator = _coordinator(DecisionMode.LEGACY, LegacyDecisionProvider(agent))
    outcome = coordinator.observe_turn(_request())
    # Legacy passes through: the policy, not this layer, rejects the ending.
    assert outcome.observation.proposal.action is RouterAction.END_SESSION
    decision = TurnPolicy().decide(
        user_text="",
        proposal=outcome.observation.proposal,
        snapshot=TurnStateSnapshot(round_count=6),
        signals=TurnSignals(),
    )
    assert decision.action.value == "chat"


# ---------------------------------------------------------------------------
# shadow
# ---------------------------------------------------------------------------


def test_shadow_mode_returns_the_authoritative_observation_only():
    agent = RecordingAgent(action="chat", confidence=0.7)
    shadow = FakeDecisionProvider(
        script=[make_decision("recommend_relaxation", probs={"recommend_relaxation": 0.9, "chat": 0.1})]
    )
    coordinator = _coordinator(
        DecisionMode.SHADOW,
        LegacyDecisionProvider(agent),
        legacy=LegacyDecisionProvider(agent),
        shadow=shadow,
    )
    outcome = coordinator.observe_turn(_request())
    assert outcome.observation.proposal.action is RouterAction.CHAT
    assert outcome.shadow is not None
    assert outcome.shadow.action == "recommend_relaxation"
    assert outcome.comparison["action_match"] is False
    assert outcome.comparison["shadow_ran"] is True
    assert len(shadow.calls) == 1


def test_shadow_mode_agreement_is_recorded():
    agent = RecordingAgent(action="chat", confidence=0.7)
    shadow = FakeDecisionProvider(script=[make_decision("chat", probs={"chat": 0.9, "end_session": 0.1})])
    coordinator = _coordinator(
        DecisionMode.SHADOW,
        LegacyDecisionProvider(agent),
        legacy=LegacyDecisionProvider(agent),
        shadow=shadow,
    )
    outcome = coordinator.observe_turn(_request())
    assert outcome.comparison["action_match"] is True


def test_shadow_failure_never_breaks_the_turn():
    agent = RecordingAgent(action="chat", confidence=0.7)
    shadow = FakeDecisionProvider(script=[RuntimeError("gateway down")])
    coordinator = _coordinator(
        DecisionMode.SHADOW,
        LegacyDecisionProvider(agent),
        legacy=LegacyDecisionProvider(agent),
        shadow=shadow,
    )
    outcome = coordinator.observe_turn(_request())
    assert outcome.observation.proposal.action is RouterAction.CHAT
    assert outcome.comparison["shadow_ran"] is False
    assert outcome.comparison["shadow_error"] == "RuntimeError"


def test_shadow_mode_requires_a_shadow_provider():
    with pytest.raises(ValueError):
        DecisionCoordinator(mode=DecisionMode.SHADOW, primary=FakeDecisionProvider())


# ---------------------------------------------------------------------------
# jev
# ---------------------------------------------------------------------------


def test_jev_mode_accepts_a_validated_decision():
    primary = FakeDecisionProvider(
        script=[make_decision("recommend_relaxation", probs={"recommend_relaxation": 0.88, "chat": 0.12})]
    )
    coordinator = _coordinator(
        DecisionMode.JEV, primary, legacy=LegacyDecisionProvider(RecordingAgent())
    )
    outcome = coordinator.observe_turn(_request())
    assert outcome.escalated is False
    assert outcome.observation.source == "jev"
    assert outcome.observation.proposal.action is RouterAction.RECOMMEND_RELAXATION


def test_jev_low_confidence_escalates_to_legacy():
    agent = RecordingAgent(action="chat", confidence=0.7)
    primary = FakeDecisionProvider(
        script=[make_decision("chat", probs={"chat": 0.40, "end_session": 0.35, "start_scale": 0.25})]
    )
    coordinator = _coordinator(DecisionMode.JEV, primary, legacy=LegacyDecisionProvider(agent))
    outcome = coordinator.observe_turn(_request())
    assert outcome.escalated is True
    assert outcome.abstained is True
    assert outcome.escalation_source == "legacy"
    assert outcome.reject_reason == "below_abstain_threshold"
    assert agent.calls == 1


def test_jev_provider_failure_escalates():
    agent = RecordingAgent(action="chat", confidence=0.7)
    primary = FakeDecisionProvider(script=[RuntimeError("gateway down")])
    coordinator = _coordinator(DecisionMode.JEV, primary, legacy=LegacyDecisionProvider(agent))
    outcome = coordinator.observe_turn(_request())
    assert outcome.escalated is True
    assert outcome.reject_reason == "provider_error"
    assert outcome.observation.proposal.action is RouterAction.CHAT


def test_jev_deterministic_escalation_does_not_call_the_agent():
    agent = RecordingAgent()
    primary = FakeDecisionProvider(script=[RuntimeError("gateway down")])
    coordinator = _coordinator(
        DecisionMode.JEV,
        primary,
        legacy=LegacyDecisionProvider(agent),
        escalation=EscalationMode.DETERMINISTIC,
    )
    outcome = coordinator.observe_turn(_request())
    assert outcome.escalation_source == "deterministic"
    assert outcome.observation.fallback_used is True
    assert agent.calls == 0


def test_jev_cannot_end_a_session():
    primary = FakeDecisionProvider(
        script=[make_decision("end_session", probs={"end_session": 0.97, "chat": 0.03})]
    )
    coordinator = _coordinator(
        DecisionMode.JEV, primary, legacy=LegacyDecisionProvider(RecordingAgent())
    )
    outcome = coordinator.observe_turn(_request())
    assert outcome.escalated is True
    assert outcome.reject_reason == "end_requires_deterministic_signal"
    assert outcome.observation.proposal.action is not RouterAction.END_SESSION


def test_jev_cannot_start_a_scale_while_one_runs():
    agent = RecordingAgent()
    primary = FakeDecisionProvider(
        script=[
            make_decision(
                "start_scale",
                scale_name="GAD-7",
                probs={"start_scale": 0.9, "chat": 0.1},
            )
        ]
    )
    coordinator = _coordinator(DecisionMode.JEV, primary, legacy=LegacyDecisionProvider(agent))
    outcome = coordinator.observe_turn(_request(active_scale="PHQ-9"))
    assert outcome.escalated is True
    assert outcome.reject_reason == "state_conflict_active_scale"


def test_jev_proposal_still_passes_through_turn_policy():
    primary = FakeDecisionProvider(
        script=[make_decision("start_scale", scale_name="PHQ-9", probs={"start_scale": 0.9, "chat": 0.1})]
    )
    coordinator = _coordinator(
        DecisionMode.JEV, primary, legacy=LegacyDecisionProvider(RecordingAgent())
    )
    outcome = coordinator.observe_turn(_request(current_round=1))
    decision = TurnPolicy().decide(
        user_text="",
        proposal=outcome.observation.proposal,
        snapshot=TurnStateSnapshot(round_count=1),
        signals=TurnSignals(),
    )
    # The model wants a scale, the policy says: too early.
    assert decision.action.value == "chat"
    assert decision.reason == "router_before_min_rounds"


def test_jev_cannot_be_configured_with_the_legacy_backend():
    agent = RecordingAgent()
    provider = LegacyDecisionProvider(agent)
    with pytest.raises(ValueError):
        DecisionCoordinator(mode=DecisionMode.JEV, primary=provider, legacy=provider)


# ---------------------------------------------------------------------------
# journal
# ---------------------------------------------------------------------------


def test_journal_records_metadata_without_text(tmp_path):
    path = tmp_path / "decision.jsonl"
    journal = DecisionJournal(path)
    coordinator = _coordinator(
        DecisionMode.JEV,
        FakeDecisionProvider(script=[make_decision("chat", probs={"chat": 0.9, "end_session": 0.1})]),
        legacy=LegacyDecisionProvider(RecordingAgent()),
        journal=journal,
    )
    coordinator.observe_turn(_request(user_text="张三住在幸福路"))
    records = journal.read()
    assert len(records) == 1
    line = json.dumps(records[0], ensure_ascii=False)
    assert "张三" not in line
    assert "幸福路" not in line
    assert records[0]["mode"] == "jev"
    assert records[0]["action"] == "chat"


def test_journal_refuses_a_text_field(tmp_path):
    journal = DecisionJournal(tmp_path / "decision.jsonl")
    with pytest.raises(DecisionJournalError):
        journal.write({"mode": "jev", "user_text": "张三"})


def test_journal_failure_never_breaks_a_turn():
    def broken(_payload):
        raise OSError("disk full")

    coordinator = _coordinator(
        DecisionMode.JEV,
        FakeDecisionProvider(script=[make_decision("chat", probs={"chat": 0.9, "end_session": 0.1})]),
        legacy=LegacyDecisionProvider(RecordingAgent()),
        journal=broken,
    )
    outcome = coordinator.observe_turn(_request())
    assert outcome.observation.proposal.action is RouterAction.CHAT


# ---------------------------------------------------------------------------
# pipeline integration
# ---------------------------------------------------------------------------


def _pipeline_with(coordinator, agent):
    from services.pipeline import ConversationPipeline

    pipeline = ConversationPipeline.__new__(ConversationPipeline)
    pipeline.agent = agent
    pipeline._agent_route_cooldown = 0
    pipeline._get_recent_dialogue_text = lambda: ""
    pipeline._get_relaxation_done = lambda: False
    pipeline._session_state_name = lambda *a, **k: "CHATTING"
    pipeline._completed_scale_names = lambda: ()
    pipeline.scale_runtime = types.SimpleNamespace(
        snapshot=lambda: types.SimpleNamespace(
            active_scale=None, answers_by_scale={}, waiting_for_answer=False
        )
    )
    pipeline._decision_coordinator_cache = (agent, coordinator)
    return pipeline


def test_pipeline_uses_the_jev_observation_when_accepted():
    agent = RecordingAgent()
    coordinator = _coordinator(
        DecisionMode.JEV,
        FakeDecisionProvider(
            script=[make_decision("recommend_relaxation", probs={"recommend_relaxation": 0.9, "chat": 0.1})]
        ),
        legacy=LegacyDecisionProvider(agent),
    )
    pipeline = _pipeline_with(coordinator, agent)
    observation = pipeline._request_agent_observation(user_text="我有点紧张", current_rounds=7)
    assert observation.proposal.action is RouterAction.RECOMMEND_RELAXATION
    assert observation.source == "jev"


def test_pipeline_shadow_mode_writes_no_session_state():
    agent = RecordingAgent(action="chat", confidence=0.7)
    shadow = FakeDecisionProvider(script=[make_decision("recommend_game", probs={"recommend_game": 0.9, "chat": 0.1})])
    coordinator = _coordinator(
        DecisionMode.SHADOW,
        LegacyDecisionProvider(agent),
        legacy=LegacyDecisionProvider(agent),
        shadow=shadow,
    )

    mutations: list[str] = []

    class ScaleSpy:
        def snapshot(self):
            return types.SimpleNamespace(
                active_scale=None, answers_by_scale={}, waiting_for_answer=False
            )

        def start(self, *_a, **_k):
            mutations.append("start")

    from services.pipeline import ConversationPipeline

    pipeline = ConversationPipeline.__new__(ConversationPipeline)
    pipeline.agent = agent
    pipeline._agent_route_cooldown = 0
    pipeline._get_recent_dialogue_text = lambda: ""
    pipeline._get_relaxation_done = lambda: False
    pipeline._session_state_name = lambda *a, **k: "CHATTING"
    pipeline._completed_scale_names = lambda: ()
    pipeline.scale_runtime = ScaleSpy()
    pipeline.session_emotions = []
    pipeline._decision_coordinator_cache = (agent, coordinator)

    observation = pipeline._request_agent_observation(user_text="我想玩个游戏", current_rounds=9)
    assert observation.proposal.action is RouterAction.CHAT
    assert mutations == []
    assert pipeline.session_emotions == []
    assert len(shadow.calls) == 1


def test_pipeline_escalates_when_the_decision_service_is_down():
    agent = RecordingAgent(action="chat", confidence=0.6)
    coordinator = _coordinator(
        DecisionMode.JEV,
        FakeDecisionProvider(script=[RuntimeError("connection refused")]),
        legacy=LegacyDecisionProvider(agent),
    )
    pipeline = _pipeline_with(coordinator, agent)
    observation = pipeline._request_agent_observation(user_text="最近很难受", current_rounds=8)
    assert observation.proposal.action is RouterAction.CHAT
    assert agent.calls == 1
