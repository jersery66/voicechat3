"""Provider-level tests: legacy adapter, gateway parsing, fake provider.

No real model and no real gateway is contacted.  The HTTP transport is replaced
by a scripted double that records the payloads, so these tests also document the
exact wire shape this project expects.
"""

from __future__ import annotations

import pytest

from conversation.agent_observation import AgentObservation
from conversation.contracts import RouterAction, RouterProposal
from decision.contracts import DecisionRequest, RawDecision
from decision.providers.anyjev import AnyJevDecisionProvider
from decision.providers.base import (
    DECISION_QUESTIONS,
    DecisionBackendUnavailable,
    DecisionProviderError,
    DecisionTransportError,
)
from decision.providers.fake import FakeDecisionProvider, make_decision, sequence_provider
from decision.providers.legacy import LegacyDecisionProvider
from decision.providers.tacit import TacitDecisionProvider


def _request(**overrides):
    payload = {"user_text": "最近总是提不起劲", "current_round": 6}
    payload.update(overrides)
    return DecisionRequest(**payload)


class FakeTransport:
    """Stand-in for ``HttpDecisionTransport``."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def post(self, payload):
        self.calls.append(payload)
        item = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        if isinstance(item, BaseException):
            raise item
        return item

    def describe(self):
        return {"transport": "fake"}


def _batch(**answers):
    """Build gateway items in DECISION_QUESTIONS order.

    The primary question is the four-way timing decision; intent and emotion
    remain in the batch as auxiliary observations only.
    """
    mapping = {
        "intervention": (
            "Continue interviewing",
            {"continue_interview": 0.85, "offer_scale": 0.15},
        ),
        "emotion": ("sad", {"sad": 0.8, "anxious": 0.2}),
        "intent": ("symptom", {"symptom": 0.7, "chat": 0.3}),
        "complex_review": ("no", {"no": 0.8, "yes": 0.2}),
    }
    mapping.update(answers)
    return [
        {"answer": answer, "probs": probs, "route": "one_forward"}
        for answer, probs in (mapping[spec.name] for spec in DECISION_QUESTIONS)
    ]


# ---------------------------------------------------------------------------
# Legacy provider
# ---------------------------------------------------------------------------


def test_legacy_provider_uses_observe_turn_when_available():
    seen = {}

    class Agent:
        model = "qwen-test"

        def is_available(self):
            return True

        def observe_turn(self, **kwargs):
            seen.update(kwargs)
            return AgentObservation(
                proposal=RouterProposal(
                    action=RouterAction.RECOMMEND_RELAXATION, confidence=0.7
                ),
                intent="counseling",
            )

    provider = LegacyDecisionProvider(Agent())
    decision = provider.decide(_request())
    assert seen["user_text"] == "最近总是提不起劲"
    assert seen["current_round"] == 6
    assert decision.action == "recommend_relaxation"
    assert decision.provider == "legacy_qwen"
    assert decision.observation is not None


def test_legacy_provider_uses_route_proposal_when_observe_turn_is_absent():
    class Agent:
        def is_available(self):
            return True

        def route_proposal(self, **_kwargs):
            return RouterProposal(action=RouterAction.CHAT, confidence=0.4)

    provider = LegacyDecisionProvider(Agent())
    decision = provider.decide(_request())
    assert decision.action == "chat"
    assert decision.confidence == pytest.approx(0.4)


def test_legacy_provider_uses_route_conversation_actions_last():
    class Agent:
        def is_available(self):
            return True

        def route_conversation_actions(self, **_kwargs):
            return {"action": "start_scale", "scale_name": "PHQ-9", "confidence": 0.8}

    provider = LegacyDecisionProvider(Agent())
    decision = provider.decide(_request())
    assert decision.action == "start_scale"
    assert decision.scale_name == "PHQ-9"


def test_legacy_provider_rejects_an_agent_with_no_known_call_shape():
    class Agent:
        def is_available(self):
            return True

    provider = LegacyDecisionProvider(Agent())
    with pytest.raises(DecisionProviderError):
        provider.decide(_request())


# ---------------------------------------------------------------------------
# AnyJev gateway
# ---------------------------------------------------------------------------


def test_anyjev_batch_request_is_assembled_into_one_decision():
    provider = AnyJevDecisionProvider(endpoint="http://127.0.0.1:8100")
    provider._http = FakeTransport([_batch()])
    decision = provider.decide(_request())
    assert decision.provider == "anyjev_decider"
    assert decision.action == "chat"
    assert decision.emotion == "sad"
    assert decision.intent_jev == "symptom"
    assert decision.clarify is False
    assert decision.confidence == pytest.approx(0.85)
    assert decision.route == "batch"
    assert sum(decision.probabilities.values()) == pytest.approx(1.0)
    assert set(decision.per_question) == {spec.name for spec in DECISION_QUESTIONS}


def test_anyjev_batch_payload_carries_state_and_options():
    provider = AnyJevDecisionProvider(endpoint="http://127.0.0.1:8100")
    transport = FakeTransport([_batch()])
    provider._http = transport
    provider.decide(_request())
    payload = transport.calls[0]
    assert "items" in payload
    assert len(payload["items"]) == len(DECISION_QUESTIONS)
    first = payload["items"][0]
    assert first["question"]
    assert isinstance(first["options"], list)
    assert "最近总是提不起劲" in first["state"]


def test_anyjev_falls_back_to_sequential_calls_when_batch_is_unsupported():
    provider = AnyJevDecisionProvider(endpoint="http://127.0.0.1:8100")
    single = {
        "answer": "Continue interviewing",
        "probs": {"Continue interviewing": 0.9, "Offer a scale assessment": 0.1},
    }
    transport = FakeTransport([single])
    provider._http = transport
    decision = provider.decide(_request())
    assert decision.route == "sequential"
    assert decision.normalized_intervention() == "continue_interview"
    assert decision.action == "chat"
    assert len(transport.calls) == len(DECISION_QUESTIONS) + 1


def test_anyjev_transport_error_propagates():
    provider = AnyJevDecisionProvider(endpoint="http://127.0.0.1:8100")
    provider._http = FakeTransport([DecisionTransportError("connection refused")])
    with pytest.raises(DecisionTransportError):
        provider.decide(_request())


def test_anyjev_refuses_a_non_loopback_endpoint():
    with pytest.raises(DecisionProviderError):
        AnyJevDecisionProvider(endpoint="http://10.0.0.5:8100")


def test_anyjev_allows_a_non_loopback_endpoint_only_when_asked():
    provider = AnyJevDecisionProvider(
        endpoint="http://10.0.0.5:8100", allow_remote_endpoint=True
    )
    assert provider.is_available() is True


def test_anyjev_server_transport_requires_an_endpoint():
    with pytest.raises(DecisionBackendUnavailable):
        AnyJevDecisionProvider(transport="server", endpoint=None)


def test_anyjev_inprocess_without_a_checkpoint_is_unavailable():
    provider = AnyJevDecisionProvider(transport="inprocess", model=None)
    assert provider.is_available() is False


def test_anyjev_inprocess_refuses_an_implicit_download():
    provider = AnyJevDecisionProvider(
        transport="inprocess", model="Qwen/Qwen3-8B", allow_model_download=False
    )
    assert provider.is_available() is False


# ---------------------------------------------------------------------------
# Tacit gateway
# ---------------------------------------------------------------------------


def test_tacit_reads_answer_probs_and_route():
    provider = TacitDecisionProvider(
        endpoint="http://127.0.0.1:8100", model="morriszjm/Tacit-2B"
    )
    provider._http = FakeTransport(
        [
            {
                "answer": "Continue interviewing",
                "probs": {"Continue interviewing": 0.9, "Offer a scale assessment": 0.1},
                "route": "one_forward",
            }
        ]
    )
    decision = provider.decide(_request())
    assert decision.provider == "tacit"
    assert decision.intervention == "Continue interviewing"
    assert decision.normalized_intervention() == "continue_interview"
    assert decision.action == "chat"
    assert decision.route == "one_forward"


def test_tacit_reports_escalation_when_the_backend_reasoned():
    provider = TacitDecisionProvider(endpoint="http://127.0.0.1:8100")
    provider._http = FakeTransport(
        [
            {
                "answer": "Continue interviewing",
                "probs": {"Continue interviewing": 0.9, "Clarify before proceeding": 0.1},
                "route": "cot",
            }
        ]
    )
    decision = provider.decide(_request())
    assert decision.route == "cot"
    assert "cot" in decision.reason


def test_tacit_rejects_a_payload_without_probabilities():
    provider = TacitDecisionProvider(endpoint="http://127.0.0.1:8100")
    provider._http = FakeTransport([{"answer": "chat"}])
    with pytest.raises(DecisionBackendUnavailable):
        provider.decide(_request())


def test_tacit_inprocess_requires_a_model():
    provider = TacitDecisionProvider(transport="inprocess", model=None)
    assert provider.is_available() is False


# ---------------------------------------------------------------------------
# Fake provider
# ---------------------------------------------------------------------------


def test_fake_provider_replays_a_script_and_then_repeats_the_last_item():
    provider = FakeDecisionProvider(
        script=[make_decision("recommend_relaxation", confidence=0.8), make_decision("chat")]
    )
    first = provider.decide(_request())
    second = provider.decide(_request())
    third = provider.decide(_request())
    assert first.action == "recommend_relaxation"
    assert second.action == "chat"
    assert third.action == "chat"
    assert len(provider.calls) == 3


def test_fake_provider_raises_a_scripted_exception():
    provider = FakeDecisionProvider(script=[DecisionTransportError("boom")])
    with pytest.raises(DecisionTransportError):
        provider.decide(_request())


def test_fake_provider_reports_unavailable_on_request():
    provider = FakeDecisionProvider(available=False)
    assert provider.is_available() is False


def test_sequence_provider_walks_through_actions():
    provider = sequence_provider(["chat", "recommend_relaxation"])
    assert provider.decide(_request()).action == "chat"
    assert provider.decide(_request()).action == "recommend_relaxation"


def test_fake_provider_never_touches_network(monkeypatch):
    def _explode(*_args, **_kwargs):
        raise AssertionError("no socket may be opened in an offline test")

    monkeypatch.setattr("socket.socket.connect", _explode)
    assert FakeDecisionProvider().decide(_request()).action == "chat"
