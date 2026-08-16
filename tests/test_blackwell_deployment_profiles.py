"""Explicit RTX PRO 6000 Blackwell deployment profile contracts."""

import importlib
import sys

import pytest

from deployment.profiles import get_deployment_profile, resolve_runtime_models
from inference.factory import build_dialogue_client
from services import llm_factory


BLACKWELL_PROFILES = (
    "rtxpro6000_96g",
    "rtxpro6000_96g_qwen38_candidate",
)


def test_blackwell_profiles_are_explicit_and_do_not_change_default_selection():
    assert get_deployment_profile().name == "dev_6g"
    assert get_deployment_profile(BLACKWELL_PROFILES[0]).name == BLACKWELL_PROFILES[0]
    assert get_deployment_profile(BLACKWELL_PROFILES[1]).name == BLACKWELL_PROFILES[1]


def test_blackwell_baseline_contract_matches_qwen25_production_values():
    profile = get_deployment_profile("rtxpro6000_96g")

    assert profile.expected_gpu_memory_gb == 96
    assert profile.runtime_backend == "vllm"
    assert profile.dialogue_model == "Qwen/Qwen2.5-72B-Instruct-AWQ"
    assert profile.dialogue_base_url == "http://127.0.0.1:8000/v1"
    assert profile.router_model == "Qwen/Qwen2.5-3B-Instruct-AWQ"
    assert profile.agent_model == "Qwen/Qwen2.5-3B-Instruct-AWQ"
    assert profile.agent_base_url == "http://127.0.0.1:8001/v1"
    assert profile.enable_streaming_tts is True
    assert profile.vllm_request_mode == "chat"
    assert profile.vllm_system_role_mode == "native"
    assert profile.dialogue_max_tokens == 1024
    assert profile.dialogue_temperature == 0.35
    assert profile.dialogue_top_p == 0.8
    assert profile.dialogue_top_k is None
    assert profile.dialogue_presence_penalty is None
    assert profile.dialogue_enable_thinking is None
    assert profile.immutable_runtime_contract is True
    assert profile.strict_preflight is True


def test_blackwell_qwen38_candidate_contract_is_explicit_opt_in():
    profile = get_deployment_profile("rtxpro6000_96g_qwen38_candidate")

    assert profile.expected_gpu_memory_gb == 96
    assert profile.runtime_backend == "vllm"
    assert profile.dialogue_model == "Qwen/Qwen3.8-27B-FP8"
    assert profile.dialogue_base_url == "http://127.0.0.1:8000/v1"
    assert profile.router_model == "Qwen/Qwen2.5-3B-Instruct-AWQ"
    assert profile.agent_model == "Qwen/Qwen2.5-3B-Instruct-AWQ"
    assert profile.agent_base_url == "http://127.0.0.1:8001/v1"
    assert profile.enable_streaming_tts is True
    assert profile.vllm_request_mode == "chat"
    assert profile.vllm_system_role_mode == "native"
    assert profile.dialogue_max_tokens == 1024
    assert profile.dialogue_temperature == 0.7
    assert profile.dialogue_top_p == 0.8
    assert profile.dialogue_top_k == 20
    assert profile.dialogue_presence_penalty == 1.5
    assert profile.dialogue_enable_thinking is False
    assert profile.immutable_runtime_contract is True
    assert profile.strict_preflight is True


@pytest.mark.parametrize("profile_name", BLACKWELL_PROFILES)
def test_blackwell_model_overrides_are_ignored(profile_name):
    profile = get_deployment_profile(profile_name)
    models = resolve_runtime_models(profile, environment={
        "VOICECHAT_DIALOGUE_MODEL": "wrong-dialogue",
        "VOICECHAT_VLLM_MODEL": "wrong-vllm",
        "OLLAMA_MODEL": "wrong-ollama",
        "AGENT_MODEL": "wrong-agent",
    })

    assert models.dialogue == profile.dialogue_model
    assert models.router == profile.router_model


@pytest.mark.parametrize("profile_name", BLACKWELL_PROFILES)
def test_blackwell_config_keeps_loopback_endpoints_despite_environment_overrides(
    monkeypatch, profile_name
):
    original = sys.modules.get("config")
    monkeypatch.setenv("VOICECHAT_DEPLOYMENT_PROFILE", profile_name)
    monkeypatch.setenv("VOICECHAT_DIALOGUE_BASE_URL", "http://wrong.example:9000/v1")
    monkeypatch.setenv("VOICECHAT_AGENT_BASE_URL", "http://wrong.example:9001/v1")
    sys.modules.pop("config", None)
    try:
        config = importlib.import_module("config")
        profile = get_deployment_profile(profile_name)

        assert config.DIALOGUE_BASE_URL == profile.dialogue_base_url
        assert config.AGENT_MODEL_SERVER == profile.agent_base_url
    finally:
        sys.modules.pop("config", None)
        if original is not None:
            sys.modules["config"] = original


@pytest.mark.parametrize("profile_name", BLACKWELL_PROFILES)
def test_blackwell_dialogue_factory_preserves_model_endpoint_and_generation_contract(
    monkeypatch, profile_name
):
    profile = get_deployment_profile(profile_name)
    monkeypatch.setenv("VOICECHAT_DIALOGUE_BASE_URL", "http://wrong.example/v1")
    client = build_dialogue_client(profile, resolve_runtime_models(profile))

    assert client.model == profile.dialogue_model
    assert client.base_url == profile.dialogue_base_url
    assert client.request_mode == profile.vllm_request_mode
    assert client.system_role_mode == profile.vllm_system_role_mode
    assert client.max_tokens == profile.dialogue_max_tokens
    assert client.dialogue_temperature == profile.dialogue_temperature
    assert client.dialogue_top_p == profile.dialogue_top_p
    assert client.dialogue_top_k == profile.dialogue_top_k
    assert client.dialogue_presence_penalty == profile.dialogue_presence_penalty
    assert client.dialogue_enable_thinking == profile.dialogue_enable_thinking


@pytest.mark.parametrize("profile_name", BLACKWELL_PROFILES)
def test_blackwell_compatibility_llm_factory_preserves_model_and_endpoint(
    monkeypatch, profile_name
):
    profile = get_deployment_profile(profile_name)
    captured = {}

    class FakeVLLMClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm_factory, "VLLMOpenAIClient", FakeVLLMClient)
    monkeypatch.setenv("VOICECHAT_DIALOGUE_BASE_URL", "http://wrong.example/v1")
    service = llm_factory.build_llm_service(profile_name=profile_name)

    assert service.model == profile.dialogue_model
    assert captured["model"] == profile.dialogue_model
    assert captured["base_url"] == profile.dialogue_base_url
    assert captured["request_mode"] == profile.vllm_request_mode
    assert captured["system_role_mode"] == profile.vllm_system_role_mode
    assert captured["max_tokens"] == profile.dialogue_max_tokens
    assert captured["dialogue_temperature"] == profile.dialogue_temperature
    assert captured["dialogue_top_p"] == profile.dialogue_top_p
    assert captured["dialogue_top_k"] == profile.dialogue_top_k
    assert captured["dialogue_presence_penalty"] == profile.dialogue_presence_penalty
    assert captured["dialogue_enable_thinking"] == profile.dialogue_enable_thinking


def test_legacy_a100_profiles_remain_unchanged():
    baseline = get_deployment_profile("a100_80g")
    candidate = get_deployment_profile("a100_80g_qwen38_candidate")

    assert baseline.expected_gpu_memory_gb == 80
    assert baseline.dialogue_model == "Qwen/Qwen2.5-72B-Instruct-AWQ"
    assert baseline.dialogue_base_url == "http://127.0.0.1:8000/v1"
    assert baseline.agent_base_url == "http://127.0.0.1:8001/v1"
    assert baseline.dialogue_temperature == 0.35
    assert baseline.dialogue_top_p == 0.8
    assert baseline.dialogue_top_k is None
    assert baseline.dialogue_presence_penalty is None
    assert baseline.dialogue_enable_thinking is None

    assert candidate.expected_gpu_memory_gb == 80
    assert candidate.dialogue_model == "Qwen/Qwen3.8-27B-FP8"
    assert candidate.dialogue_temperature == 0.7
    assert candidate.dialogue_top_p == 0.8
    assert candidate.dialogue_top_k == 20
    assert candidate.dialogue_presence_penalty == 1.5
    assert candidate.dialogue_enable_thinking is False


def test_dev_profiles_remain_mutable_and_non_strict():
    for profile_name in ("dev_6g", "dev_vllm_6g"):
        profile = get_deployment_profile(profile_name)
        assert profile.immutable_runtime_contract is False
        assert profile.strict_preflight is False
