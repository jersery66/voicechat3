"""Cross-factory compatibility matrix for dialogue deployment profiles."""

from types import SimpleNamespace

import pytest

from deployment.profiles import get_deployment_profile, resolve_runtime_models
from inference.factory import build_dialogue_client
from inference.vllm_client import VLLMOpenAIClient
from services.llm_factory import build_llm_service


PROFILE_NAMES = ("dev_6g", "dev_vllm_6g", "a100_80g", "a100_80g_qwen38_candidate")
A100_NAMES = ("a100_80g", "a100_80g_qwen38_candidate")


def _capture_llm_factory(monkeypatch, profile_name: str, **env):
    captured = {}

    class FakeVLLMClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("services.llm_factory.VLLMOpenAIClient", FakeVLLMClient)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    service = build_llm_service(profile_name=profile_name)
    return service, captured


def _request_capture(client: VLLMOpenAIClient):
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return iter([])

    client._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    list(client.stream_messages(messages=[{"role": "user", "content": "hello"}]))
    return calls[0]


def test_compat_01_supported_profile_matrix_is_explicit():
    assert tuple(sorted(PROFILE_NAMES)) == tuple(sorted({
        get_deployment_profile(name).name for name in PROFILE_NAMES
    }))


def test_compat_02_a100_production_roles_remain_pinned():
    profile = get_deployment_profile("a100_80g")
    models = resolve_runtime_models(profile, environment={
        "VOICECHAT_DIALOGUE_MODEL": "wrong-dialogue",
        "VOICECHAT_VLLM_MODEL": "wrong-vllm",
        "OLLAMA_MODEL": "wrong-ollama",
        "AGENT_MODEL": "wrong-agent",
    })
    assert profile.dialogue_model == "Qwen/Qwen2.5-72B-Instruct-AWQ"
    assert models.dialogue == profile.dialogue_model
    assert models.router == "Qwen/Qwen2.5-3B-Instruct-AWQ"


def test_compat_03_qwen38_candidate_roles_remain_pinned():
    profile = get_deployment_profile("a100_80g_qwen38_candidate")
    models = resolve_runtime_models(profile, environment={
        "VOICECHAT_DIALOGUE_MODEL": "wrong-dialogue",
        "VOICECHAT_VLLM_MODEL": "wrong-vllm",
        "OLLAMA_MODEL": "wrong-ollama",
        "AGENT_MODEL": "wrong-agent",
    })
    assert profile.dialogue_model == "Qwen/Qwen3.8-27B-FP8"
    assert models.dialogue == profile.dialogue_model
    assert models.router == "Qwen/Qwen2.5-3B-Instruct-AWQ"


def test_compat_04_qwen38_is_not_an_implicit_default():
    assert get_deployment_profile().name == "dev_6g"
    assert get_deployment_profile("a100_80g").name == "a100_80g"


@pytest.mark.parametrize("profile_name", A100_NAMES)
def test_compat_05_inference_factory_pins_a100_dialogue_endpoint(monkeypatch, profile_name):
    monkeypatch.setenv("VOICECHAT_DIALOGUE_BASE_URL", "http://malicious-or-wrong.example/v1")
    profile = get_deployment_profile(profile_name)
    client = build_dialogue_client(profile, resolve_runtime_models(profile))
    assert client.base_url == profile.dialogue_base_url == "http://127.0.0.1:8000/v1"


def test_compat_06_inference_factory_allows_dev_vllm_endpoint_override(monkeypatch):
    monkeypatch.setenv("VOICECHAT_DIALOGUE_BASE_URL", "http://dev-override.example/v1")
    profile = get_deployment_profile("dev_vllm_6g")
    client = build_dialogue_client(profile, resolve_runtime_models(profile))
    assert client.base_url == "http://dev-override.example/v1"


@pytest.mark.parametrize("profile_name", A100_NAMES)
def test_compat_07_llm_factory_pins_a100_dialogue_endpoint(monkeypatch, profile_name):
    service, captured = _capture_llm_factory(
        monkeypatch,
        profile_name,
        VOICECHAT_DIALOGUE_BASE_URL="http://malicious-or-wrong.example/v1",
    )
    assert service.model == get_deployment_profile(profile_name).dialogue_model
    assert captured["base_url"] == "http://127.0.0.1:8000/v1"


def test_compat_08_llm_factory_allows_dev_vllm_endpoint_override(monkeypatch):
    _service, captured = _capture_llm_factory(
        monkeypatch,
        "dev_vllm_6g",
        VOICECHAT_DIALOGUE_BASE_URL="http://dev-override.example/v1",
    )
    assert captured["base_url"] == "http://dev-override.example/v1"


def test_compat_09_candidate_and_llm_factory_generation_fields_match():
    profile = get_deployment_profile("a100_80g_qwen38_candidate")
    client = build_dialogue_client(profile, resolve_runtime_models(profile))
    assert (
        client.model,
        client.base_url,
        client.request_mode,
        client.system_role_mode,
        client.max_tokens,
        client.dialogue_temperature,
        client.dialogue_top_p,
        client.dialogue_top_k,
        client.dialogue_presence_penalty,
        client.dialogue_enable_thinking,
    ) == (
        profile.dialogue_model,
        profile.dialogue_base_url,
        profile.vllm_request_mode,
        profile.vllm_system_role_mode,
        profile.dialogue_max_tokens,
        profile.dialogue_temperature,
        profile.dialogue_top_p,
        profile.dialogue_top_k,
        profile.dialogue_presence_penalty,
        profile.dialogue_enable_thinking,
    )


def test_compat_10_qwen25_request_keeps_legacy_generation_body():
    profile = get_deployment_profile("a100_80g")
    client = VLLMOpenAIClient.__new__(VLLMOpenAIClient)
    client.model = profile.dialogue_model
    client.max_tokens = profile.dialogue_max_tokens
    client.dialogue_temperature = profile.dialogue_temperature
    client.dialogue_top_p = profile.dialogue_top_p
    client.dialogue_top_k = profile.dialogue_top_k
    client.dialogue_presence_penalty = profile.dialogue_presence_penalty
    client.dialogue_enable_thinking = profile.dialogue_enable_thinking
    request = _request_capture(client)
    assert request["temperature"] == 0.35
    assert request["top_p"] == 0.8
    assert "extra_body" not in request


def test_compat_11_dev_completion_profile_contract_remains_unchanged():
    profile = get_deployment_profile("dev_vllm_6g")
    assert profile.vllm_request_mode == "completion"
    assert profile.system_prompt_override == "Reply briefly and directly to the user."
    assert profile.dialogue_max_tokens == 96


def test_compat_12_agent_endpoint_stays_separate_from_dialogue_endpoint():
    for profile_name in A100_NAMES:
        profile = get_deployment_profile(profile_name)
        assert profile.dialogue_base_url == "http://127.0.0.1:8000/v1"
        assert profile.agent_base_url == "http://127.0.0.1:8001/v1"


def test_compat_13_candidate_keeps_streaming_tts_and_native_chat():
    profile = get_deployment_profile("a100_80g_qwen38_candidate")
    assert profile.enable_streaming_tts is True
    assert profile.vllm_request_mode == "chat"
    assert profile.vllm_system_role_mode == "native"


def test_compat_14_frozen_prompt_and_business_authority_tests_remain_external():
    from config import SYSTEM_PROMPT

    assert "本轮动作已经由系统确定" in SYSTEM_PROMPT
    assert get_deployment_profile("a100_80g").dialogue_model == "Qwen/Qwen2.5-72B-Instruct-AWQ"


def test_compat_15_real_qwen38_acceptance_is_not_claimed_by_profile_tests():
    assert get_deployment_profile("a100_80g_qwen38_candidate").notes.endswith("unvalidated.")
