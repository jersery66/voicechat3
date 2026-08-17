"""Correction B1: one ordinary-turn Agent observation."""

from __future__ import annotations

from conversation.agent_observation import AgentObservation
from conversation.contracts import RouterAction, RouterProposal, TurnAction
from services.pipeline import ConversationPipeline, PipelineConfig
from tests.integration.fakes import FakeData, FakeLLM, FakeRAG, FakeReport, FakeTTS


class CountingObservationAgent:
    def __init__(self, *, available=True, broken=False):
        self.available = available
        self.broken = broken
        self.observe_calls = 0
        self.route_calls = 0
        self.intent_calls = 0
        self.emotion_calls = 0

    def is_available(self):
        return self.available

    def observe_turn(self, **_kwargs):
        self.observe_calls += 1
        if self.broken:
            raise ValueError("malformed structured observation")
        return AgentObservation(
            proposal=RouterProposal(
                action=RouterAction.CHAT,
                emotion="sad",
                intensity=0.65,
                needs_rag=False,
                confidence=0.8,
                reason="one observation",
            ),
            intent="counseling",
        )

    def classify_intent(self, _text):
        self.intent_calls += 1
        raise AssertionError("secondary intent inference is forbidden")

    def detect_emotion(self, _text):
        self.emotion_calls += 1
        raise AssertionError("secondary emotion inference is forbidden")


def _pipeline(agent):
    return ConversationPipeline(
        stt_service=None,
        llm_service=FakeLLM(["测试回应。"]),
        tts_service=FakeTTS(),
        rag_service=FakeRAG(),
        agent_service=agent,
        report_service=FakeReport(start_round=0),
        data_manager=FakeData(),
        session_emotions=[],
    )


def test_ordinary_turn_uses_one_observation_and_no_secondary_calls():
    agent = CountingObservationAgent()
    pipeline = _pipeline(agent)
    try:
        result = pipeline.execute(PipelineConfig(user_text="合成输入"), lambda *_: None)
        assert agent.observe_calls == 1
        assert agent.intent_calls == 0
        assert agent.emotion_calls == 0
        assert result.agent_observation is not None
        assert result.intent == "counseling"
        assert result.emotion_result == {"emotion": "sad", "intensity": 0.65}
    finally:
        pipeline.shutdown()


def test_observation_proposal_reaches_policy_unchanged():
    agent = CountingObservationAgent()
    pipeline = _pipeline(agent)
    try:
        # The observation proposal is CHAT and therefore cannot execute a
        # scale action merely because emotion is sad/intense.
        result = pipeline.execute(PipelineConfig(user_text="低落"), lambda *_: None)
        assert result.router_proposal.action is RouterAction.CHAT
        assert result.turn_decision.action is TurnAction.CHAT
    finally:
        pipeline.shutdown()


def test_agent_unavailable_uses_local_fallback_without_secondary_calls():
    agent = CountingObservationAgent(available=False)
    pipeline = _pipeline(agent)
    try:
        result = pipeline.execute(PipelineConfig(user_text="低落"), lambda *_: None)
        assert result.agent_observation.fallback_used is True
        assert result.agent_observation.source == "deterministic_fallback"
        assert agent.observe_calls == 0
        assert agent.intent_calls == 0
        assert agent.emotion_calls == 0
        assert result.turn_decision.action is TurnAction.CHAT
    finally:
        pipeline.shutdown()


def test_malformed_observation_uses_fallback_without_retrying_other_agent_calls():
    agent = CountingObservationAgent(broken=True)
    pipeline = _pipeline(agent)
    try:
        result = pipeline.execute(PipelineConfig(user_text="合成输入"), lambda *_: None)
        assert result.agent_observation.fallback_used is True
        assert agent.observe_calls == 1
        assert agent.intent_calls == 0
        assert agent.emotion_calls == 0
    finally:
        pipeline.shutdown()


def test_legacy_apis_remain_independently_callable():
    agent = CountingObservationAgent()
    # The compatibility surface remains present even though the pipeline does
    # not call it in ordinary turns.
    assert callable(agent.classify_intent)
    assert callable(agent.detect_emotion)


def test_production_agent_observe_turn_projects_one_route_response():
    from services.agent_service import AgentService

    service = AgentService.__new__(AgentService)
    calls = []

    def route(**kwargs):
        calls.append(kwargs)
        return {
            "action": "chat",
            "intent": "counseling",
            "emotion": "sad",
            "intensity": 0.6,
            "needs_rag": False,
            "confidence": 0.8,
        }

    service.route_conversation_actions = route
    observation = service.observe_turn(user_text="合成输入")
    assert len(calls) == 1
    assert observation.intent == "counseling"
    assert observation.proposal.emotion == "sad"
    assert observation.proposal.needs_rag is False


def test_internal_route_failure_projects_keyword_emotion_in_fallback():
    from services.agent_service import AgentService

    service = AgentService.__new__(AgentService)
    calls = {"route": 0, "intent": 0, "emotion": 0}

    def route(**_kwargs):
        calls["route"] += 1
        return {"fallback_used": True, "reason": "route failed"}

    def keyword_intent(_text):
        calls["intent"] += 1
        return {"intent": "counseling", "confidence": 1.0}

    def keyword_emotion(_text):
        calls["emotion"] += 1
        return {"emotion": "anxious", "intensity": 0.8, "keywords": ["焦虑"]}

    service.route_conversation_actions = route
    service._keyword_classify = keyword_intent
    service._keyword_detect_emotion = keyword_emotion

    observation = service.observe_turn(user_text="我现在特别焦虑，心里很慌")

    assert calls == {"route": 1, "intent": 1, "emotion": 1}
    assert observation.fallback_used is True
    assert observation.source == "deterministic_fallback"
    assert observation.intent == "counseling"
    assert observation.proposal.action is RouterAction.CHAT
    assert observation.proposal.emotion == "anxious"
    assert observation.proposal.intensity == 0.8
    assert observation.proposal.needs_rag is False


def test_internal_route_failure_does_not_retry_secondary_agent_calls():
    from services.agent_service import AgentService

    service = AgentService.__new__(AgentService)
    service.route_conversation_actions = lambda **_kwargs: {"fallback_used": True}
    service._keyword_classify = lambda _text: {"intent": "counseling"}
    service._keyword_detect_emotion = lambda _text: {
        "emotion": "anxious",
        "intensity": 0.8,
    }
    service._call_json = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("fallback must not issue a secondary model call")
    )

    observation = service.observe_turn(user_text="我现在特别焦虑")

    assert observation.proposal.emotion == "anxious"
    assert observation.proposal.intensity == 0.8


def test_actual_route_exception_uses_one_model_attempt_then_local_observation():
    from services.agent_service import AgentService

    class BrokenCompletions:
        calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            raise RuntimeError("synthetic route outage")

    class Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": BrokenCompletions()})()

    service = AgentService.__new__(AgentService)
    service.client = Client()
    service.model = "synthetic-agent"

    observation = service.observe_turn(user_text="我现在特别焦虑")

    assert service.client.chat.completions.calls == 1
    assert observation.fallback_used is True
    assert observation.proposal.emotion == "anxious"
    assert observation.proposal.intensity == 0.75


def test_agent_unavailable_path_projects_the_same_keyword_emotion():
    from services.agent_service import AgentService

    agent = AgentService.__new__(AgentService)
    agent.is_available = lambda: False
    pipeline = _pipeline(agent)
    try:
        result = pipeline.execute(
            PipelineConfig(user_text="我现在特别焦虑，心里很慌"),
            lambda *_: None,
        )
        assert result.agent_observation.fallback_used is True
        assert result.agent_observation.source == "deterministic_fallback"
        assert result.agent_observation.proposal.action is RouterAction.CHAT
        assert result.emotion_result == {"emotion": "anxious", "intensity": 0.75}
        assert result.turn_decision.action is TurnAction.CHAT
    finally:
        pipeline.shutdown()
