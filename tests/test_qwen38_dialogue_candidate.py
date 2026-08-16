"""Compatibility contract for the opt-in Qwen3.8 dialogue profile."""

from types import SimpleNamespace

from deployment.profiles import get_deployment_profile, resolve_runtime_models
from inference.factory import build_dialogue_client
from inference.vllm_client import VLLMOpenAIClient


def test_q38_01_production_a100_profile_remains_qwen25_72b():
    profile = get_deployment_profile("a100_80g")
    assert profile.dialogue_model == "Qwen/Qwen2.5-72B-Instruct-AWQ"
    assert resolve_runtime_models(profile, environment={}).dialogue == profile.dialogue_model


def test_q38_02_candidate_profile_selects_qwen38_fp8():
    profile = get_deployment_profile("a100_80g_qwen38_candidate")
    assert profile.dialogue_model == "Qwen/Qwen3.8-27B-FP8"
    assert profile.expected_gpu_memory_gb == 80
    assert profile.runtime_backend == "vllm"


def test_q38_03_candidate_keeps_qwen25_router_and_agent():
    profile = get_deployment_profile("a100_80g_qwen38_candidate")
    models = resolve_runtime_models(profile, environment={})
    assert profile.router_model == "Qwen/Qwen2.5-3B-Instruct-AWQ"
    assert profile.agent_model == "Qwen/Qwen2.5-3B-Instruct-AWQ"
    assert models.router == "Qwen/Qwen2.5-3B-Instruct-AWQ"


def test_q38_04_candidate_is_explicit_and_never_default():
    assert get_deployment_profile().name == "dev_6g"
    assert get_deployment_profile("a100_80g").name == "a100_80g"


def test_q38_05_candidate_ignores_unauthorized_model_override():
    profile = get_deployment_profile("a100_80g_qwen38_candidate")
    models = resolve_runtime_models(
        profile,
        environment={
            "VOICECHAT_DIALOGUE_MODEL": "unauthorized-model",
            "OLLAMA_MODEL": "stale-model",
            "AGENT_MODEL": "unauthorized-agent",
        },
    )
    assert models.dialogue == "Qwen/Qwen3.8-27B-FP8"
    assert models.router == "Qwen/Qwen2.5-3B-Instruct-AWQ"


def _fake_chat_client(calls, chunks=None, completion_text="reply"):
    chunks = chunks or [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"))])]

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if kwargs.get("stream"):
                return iter(chunks)
            return SimpleNamespace(choices=[
                SimpleNamespace(message=SimpleNamespace(content=completion_text))
            ])

    client = VLLMOpenAIClient.__new__(VLLMOpenAIClient)
    client.model = "Qwen/Qwen3.8-27B-FP8"
    client.request_mode = "chat"
    client.system_role_mode = "native"
    client.max_tokens = 64
    client.dialogue_temperature = 0.7
    client.dialogue_top_p = 0.8
    client.dialogue_top_k = 20
    client.dialogue_presence_penalty = 1.5
    client.dialogue_enable_thinking = False
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    return client


def test_q38_06_qwen25_baseline_request_keeps_legacy_sampling():
    calls = []
    client = VLLMOpenAIClient.__new__(VLLMOpenAIClient)
    client.model = "Qwen/Qwen2.5-72B-Instruct-AWQ"
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: calls.append(kwargs) or iter([])
    )))
    list(client.stream_messages(messages=[{"role": "user", "content": "hi"}]))
    assert calls[0]["temperature"] == 0.35
    assert calls[0]["top_p"] == 0.8
    assert "extra_body" not in calls[0]


def test_q38_08_candidate_stream_sends_non_thinking_body():
    calls = []
    client = _fake_chat_client(calls)
    assert list(client.stream_messages(messages=[
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hello"},
    ])) == ["ok"]
    assert calls[0]["temperature"] == 0.7
    assert calls[0]["presence_penalty"] == 1.5
    assert calls[0]["extra_body"] == {
        "top_k": 20,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def test_q38_09_candidate_complete_sends_non_thinking_body():
    calls = []
    client = _fake_chat_client(calls)
    assert client.complete_messages(
        messages=[{"role": "user", "content": "hello"}], max_tokens=12
    ) == "reply"
    assert calls[0]["temperature"] == 0.7
    assert calls[0]["presence_penalty"] == 1.5
    assert calls[0]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_q38_12_stream_and_complete_share_candidate_generation_settings():
    calls = []
    client = _fake_chat_client(calls)
    list(client.stream_messages(messages=[{"role": "user", "content": "hello"}]))
    client.complete_messages(messages=[{"role": "user", "content": "hello"}], max_tokens=12)
    for call in calls:
        assert call["temperature"] == 0.7
        assert call["top_p"] == 0.8
        assert call["presence_penalty"] == 1.5
        assert call["extra_body"] == {
            "top_k": 20,
            "chat_template_kwargs": {"enable_thinking": False},
        }


def test_q38_13_candidate_preserves_complete_message_history():
    calls = []
    client = _fake_chat_client(calls)
    messages = [
        {"role": "system", "content": "frozen prompt"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "second"},
    ]
    list(client.stream_messages(messages=messages))
    assert calls[0]["messages"] == messages


def test_q38_14_candidate_consumes_delta_content_not_reasoning_fields():
    calls = []
    chunks = [SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(reasoning_content="hidden", content="visible")
    )])]
    client = _fake_chat_client(calls, chunks=chunks)
    assert list(client.stream_messages(messages=[{"role": "user", "content": "hello"}])) == ["visible"]


def test_q38_15_candidate_keeps_dialogue_and_router_ports():
    profile = get_deployment_profile("a100_80g_qwen38_candidate")
    assert profile.dialogue_base_url == "http://127.0.0.1:8000/v1"
    assert profile.agent_base_url == "http://127.0.0.1:8001/v1"


def test_q38_16_candidate_factory_transfers_generation_contract():
    profile = get_deployment_profile("a100_80g_qwen38_candidate")
    client = build_dialogue_client(profile, resolve_runtime_models(profile))
    assert client.model == "Qwen/Qwen3.8-27B-FP8"
    assert client.dialogue_top_k == 20
    assert client.dialogue_temperature == 0.7
    assert client.dialogue_presence_penalty == 1.5
    assert client.dialogue_enable_thinking is False


def test_q38_17_candidate_keeps_streaming_tts_enabled_without_changing_provider():
    profile = get_deployment_profile("a100_80g_qwen38_candidate")
    assert profile.enable_streaming_tts is True


def test_q38_18_prompt_contract_remains_external_to_candidate_profile():
    from config import SYSTEM_PROMPT

    assert "本轮动作已经由系统确定" in SYSTEM_PROMPT
    assert get_deployment_profile("a100_80g").dialogue_model == "Qwen/Qwen2.5-72B-Instruct-AWQ"
