"""Deployment immutability and preflight policy belong to profile data."""

from dataclasses import replace
from types import SimpleNamespace

from deployment.profiles import get_deployment_profile, resolve_runtime_models
from inference.factory import build_dialogue_client
from services import llm_factory
from scripts import check_config


def test_current_profiles_declare_immutability_and_preflight_policy():
    assert (
        get_deployment_profile("dev_6g").immutable_runtime_contract,
        get_deployment_profile("dev_6g").strict_preflight,
    ) == (False, False)
    assert (
        get_deployment_profile("dev_vllm_6g").immutable_runtime_contract,
        get_deployment_profile("dev_vllm_6g").strict_preflight,
    ) == (False, False)
    for name in ("a100_80g", "a100_80g_qwen38_candidate"):
        profile = get_deployment_profile(name)
        assert profile.immutable_runtime_contract is True
        assert profile.strict_preflight is True


def test_renamed_immutable_profile_still_pins_models_and_dialogue_endpoint(monkeypatch):
    profile = replace(
        get_deployment_profile("a100_80g"),
        name="future_blackwell_profile",
    )
    monkeypatch.setenv("VOICECHAT_DIALOGUE_MODEL", "wrong-dialogue")
    monkeypatch.setenv("AGENT_MODEL", "wrong-agent")
    monkeypatch.setenv("VOICECHAT_DIALOGUE_BASE_URL", "http://wrong.example/v1")

    models = resolve_runtime_models(profile)
    client = build_dialogue_client(profile, models)

    assert models.dialogue == profile.dialogue_model
    assert models.router == profile.router_model
    assert client.base_url == profile.dialogue_base_url


def test_renamed_immutable_profile_pins_compatibility_llm_factory(monkeypatch):
    profile = replace(
        get_deployment_profile("a100_80g"),
        name="future_blackwell_profile",
    )
    captured = {}

    class FakeVLLMClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm_factory, "get_deployment_profile", lambda _name=None: profile)
    monkeypatch.setattr(llm_factory, "VLLMOpenAIClient", FakeVLLMClient)
    monkeypatch.setenv("VOICECHAT_DIALOGUE_BASE_URL", "http://wrong.example/v1")

    llm_factory.build_llm_service(profile_name=profile.name)

    assert captured["base_url"] == profile.dialogue_base_url


def test_check_config_uses_profile_owned_strict_preflight(monkeypatch):
    monkeypatch.setattr(check_config, "_check_openai_compatible_model", lambda *_: False)
    monkeypatch.setattr(check_config, "get_deployment_profile", lambda: SimpleNamespace(
        name="future_strict_profile",
        strict_preflight=True,
    ))
    monkeypatch.setattr("config.AGENT_BACKEND", "vllm")
    monkeypatch.setattr("config.AGENT_MODEL", "agent-model")
    monkeypatch.setattr("config.AGENT_MODEL_SERVER", "http://agent:8001/v1")

    assert check_config.check_agent_backend() is False
