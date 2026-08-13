"""vLLM-backed optional guard remains subordinate to deterministic policy."""

from types import SimpleNamespace

from deployment.profiles import get_deployment_profile, resolve_runtime_models
from inference.factory import build_guard_client, build_safety_gate
from inference.vllm_guard_client import VLLMGuardClient
from safety.safety_gate import SafetyGate
from safety.types import SafetyAction


def test_a100_guard_client_uses_its_own_profile_owned_vllm_endpoint():
    profile = get_deployment_profile("a100_80g")

    guard = build_guard_client(profile, resolve_runtime_models(profile, environment={}))

    assert guard is None


def test_guard_is_not_created_when_a_profile_does_not_select_one():
    profile = get_deployment_profile("dev_vllm_6g")

    assert build_guard_client(profile, resolve_runtime_models(profile, environment={})) is None


def test_factory_keeps_a100_safety_boundary_deterministic():
    profile = get_deployment_profile("a100_80g")

    gate = build_safety_gate(profile, resolve_runtime_models(profile, environment={}))

    assert gate._guard_client is None


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
