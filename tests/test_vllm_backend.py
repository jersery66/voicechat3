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
    assert models.router == "Qwen/Qwen2.5-3B-Instruct-AWQ"
    assert profile.agent_base_url == "http://127.0.0.1:8001/v1"


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


def test_vllm_client_lists_served_model_ids():
    class Models:
        def list(self):
            return SimpleNamespace(data=[
                SimpleNamespace(id="gemma-2b-awq"),
                SimpleNamespace(id="another-model"),
            ])

    client = VLLMOpenAIClient.__new__(VLLMOpenAIClient)
    client._client = SimpleNamespace(models=Models())

    assert getattr(client, "list_model_ids", lambda: [])() == [
        "gemma-2b-awq",
        "another-model",
    ]


def test_vllm_client_completes_a_short_non_streaming_message():
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[
                SimpleNamespace(message=SimpleNamespace(content="short reply"))
            ])

    client = VLLMOpenAIClient.__new__(VLLMOpenAIClient)
    client.model = "test-model"
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    assert getattr(client, "complete_messages", lambda **_: "")(
        messages=[{"role": "user", "content": "hello"}], max_tokens=7
    ) == "short reply"
    assert calls[0]["stream"] is False
    assert calls[0]["max_tokens"] == 7


def test_vllm_client_warmup_requires_its_configured_model_to_be_served():
    client = VLLMOpenAIClient.__new__(VLLMOpenAIClient)
    client.model = "expected-model"
    client.list_model_ids = lambda: ["some-other-model"]

    assert getattr(client, "warmup", lambda: True)() is False


def test_vllm_client_streams_the_completion_endpoint_when_profile_selects_it():
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return iter([
                SimpleNamespace(choices=[SimpleNamespace(text="4")]),
            ])

    client = VLLMOpenAIClient.__new__(VLLMOpenAIClient)
    client.model = "gemma-2b-awq"
    client.request_mode = "completion"
    client.max_tokens = 32
    client._client = SimpleNamespace(completions=Completions())

    assert list(client.stream_messages(messages=[
        {"role": "system", "content": "Reply briefly."},
        {"role": "user", "content": "What is 2+2?"},
    ])) == ["4"]
    assert calls[0]["stream"] is True
    assert calls[0]["max_tokens"] == 32
    assert calls[0]["prompt"].endswith("Assistant:")
    assert "User: What is 2+2?" in calls[0]["prompt"]


def test_vllm_client_folds_system_text_into_first_user_turn_for_templates_without_system_role():
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return iter([
                SimpleNamespace(choices=[
                    SimpleNamespace(delta=SimpleNamespace(content="reply"))
                ]),
            ])

    client = VLLMOpenAIClient.__new__(VLLMOpenAIClient)
    client.model = "gemma-2b-it-awq"
    client.request_mode = "chat"
    client.system_role_mode = "prepend_user"
    client.max_tokens = 32
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    )

    assert list(client.stream_messages(messages=[
        {"role": "system", "content": "Reply briefly."},
        {"role": "user", "content": "What is 2+2?"},
    ])) == ["reply"]
    assert calls[0]["messages"] == [
        {"role": "user", "content": "Reply briefly.\n\nWhat is 2+2?"},
    ]


def test_factory_builds_vllm_client_only_for_a_vllm_profile():
    a100 = get_deployment_profile("a100_80g")
    dev = get_deployment_profile("dev_6g")

    assert isinstance(build_dialogue_client(a100, resolve_runtime_models(a100)), VLLMOpenAIClient)
    assert build_dialogue_client(dev, resolve_runtime_models(dev)) is None


def test_vllm_factory_pins_a100_dialogue_endpoint_against_override(monkeypatch):
    profile = get_deployment_profile("a100_80g")
    monkeypatch.setenv("VOICECHAT_DIALOGUE_BASE_URL", "http://a100-host:8000/v1")

    client = build_dialogue_client(profile, resolve_runtime_models(profile))

    assert client.base_url == "http://127.0.0.1:8000/v1"
