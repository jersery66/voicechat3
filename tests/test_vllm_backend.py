"""vLLM/OpenAI-compatible backend tests without a running GPU server."""

from types import SimpleNamespace

from deployment.profiles import get_deployment_profile, resolve_runtime_models
from inference.factory import build_dialogue_client
from inference.vllm_client import VLLMOpenAIClient


def test_a100_profile_uses_vllm_for_dialogue_and_keeps_router_explicit():
    profile = get_deployment_profile("a100_80g")
    models = resolve_runtime_models(profile, environment={})

    assert profile.runtime_backend == "vllm"
    assert models.dialogue == "Qwen/Qwen2.5-72B-Instruct-AWQ"
    assert models.router == "qwen2.5:3b"


def test_vllm_client_streams_openai_compatible_chunks_and_preserves_messages():
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return iter([
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="hel"))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="lo"))]),
            ])

    client = VLLMOpenAIClient.__new__(VLLMOpenAIClient)
    client.model = "test-model"
    client.base_url = "http://a100:8000/v1"
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    assert list(client.stream_reply(user_text="hello", system_context="system")) == ["hel", "lo"]
    assert calls[0]["model"] == "test-model"
    assert calls[0]["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hello"},
    ]
    assert calls[0]["stream"] is True


def test_vllm_client_handles_empty_delta_content():
    class Completions:
        def create(self, **kwargs):
            return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))])])

    client = VLLMOpenAIClient.__new__(VLLMOpenAIClient)
    client.model = "test-model"
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    assert list(client.stream_reply(user_text="hello")) == []


def test_factory_builds_vllm_client_only_for_a_vllm_profile():
    a100 = get_deployment_profile("a100_80g")
    dev = get_deployment_profile("dev_6g")

    assert isinstance(build_dialogue_client(a100, resolve_runtime_models(a100)), VLLMOpenAIClient)
    assert build_dialogue_client(dev, resolve_runtime_models(dev)) is None


def test_vllm_factory_honours_the_shared_dialogue_endpoint_override(monkeypatch):
    profile = get_deployment_profile("a100_80g")
    monkeypatch.setenv("VOICECHAT_DIALOGUE_BASE_URL", "http://a100-host:8000/v1")

    client = build_dialogue_client(profile, resolve_runtime_models(profile))

    assert client.base_url == "http://a100-host:8000/v1"
