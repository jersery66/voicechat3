"""The retained Guard adapter is explicitly confined to the legacy safety namespace."""

from types import SimpleNamespace

from inference import factory
from safety.safety_gate import SafetyGate
from safety.types import SafetyAction
from safety.vllm_guard_client import VLLMGuardClient


def test_guard_is_constructed_directly_from_the_legacy_safety_namespace():
    guard = VLLMGuardClient.__new__(VLLMGuardClient)

    assert isinstance(guard, VLLMGuardClient)


def test_production_inference_factory_does_not_expose_guard_builders():
    assert not hasattr(factory, "build_guard_client")
    assert not hasattr(factory, "build_safety_gate")


def test_guard_parses_qwen_guard_self_harm_classification_without_overriding_crisis_policy():
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(
                    content="Safety: Unsafe\nCategories: Suicide & Self-Harm"
                )
            )])

    guard = VLLMGuardClient.__new__(VLLMGuardClient)
    guard.model = "Qwen/Qwen3Guard-Gen-4B"
    guard.base_url = "http://guard:8002/v1"
    guard._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    decision = guard.assess_input("我不知道还能不能撑下去")

    assert decision.source == "guard_model"
    assert decision.self_harm_signal is True
    assert decision.risk_level == 3
    assert decision.action == SafetyAction.MONITOR
    assert calls[0]["model"] == "Qwen/Qwen3Guard-Gen-4B"
    assert calls[0]["messages"] == [{"role": "user", "content": "我不知道还能不能撑下去"}]


def test_guard_error_never_removes_the_deterministic_safety_boundary():
    class Completions:
        def create(self, **kwargs):
            raise RuntimeError("guard unavailable")

    guard = VLLMGuardClient.__new__(VLLMGuardClient)
    guard.model = "Qwen/Qwen3Guard-Gen-4B"
    guard._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    decision = guard.assess_input("normal text")

    assert decision.source == "guard_model"
    assert decision.risk_level == 0
    assert decision.uncertainty is True


def test_guard_unavailability_preserves_a_deterministic_emergency_decision():
    class Completions:
        def create(self, **kwargs):
            raise RuntimeError("guard unavailable")

    guard = VLLMGuardClient.__new__(VLLMGuardClient)
    guard.model = "Qwen/Qwen3Guard-Gen-4B"
    guard._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    decision = SafetyGate(guard_client=guard).assess_input("我准备今晚割腕")

    assert decision.action == SafetyAction.EMERGENCY
    assert decision.risk_level == 9
