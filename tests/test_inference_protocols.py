"""Dialogue/router adapters stay swappable; the legacy Guard is safety-owned."""

from inference.dialogue_client import DialogueClient
from inference.router_client import RouterClient
from safety.guard_client import GuardClient
from safety.types import SafetyDecision


class FakeDialogue:
    def stream_reply(self, *, user_text, system_context=""):
        yield "reply"


class FakeRouter:
    def route(self, *, user_text, recent_history=""):
        return {"action": "chat"}


class FakeGuard:
    def assess_input(self, text):
        return SafetyDecision()


def test_inference_protocols_do_not_depend_on_ollama_or_openai_clients():
    assert isinstance(FakeDialogue(), DialogueClient)
    assert isinstance(FakeRouter(), RouterClient)
    assert isinstance(FakeGuard(), GuardClient)
