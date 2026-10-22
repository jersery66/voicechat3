"""OpenAI-compatible backend tests without a running model server."""

from types import SimpleNamespace

from inference.openai_client import OpenAICompatibleClient


def test_client_streams_openai_compatible_chunks_and_preserves_messages():
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return iter([
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="hel"))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="lo"))]),
            ])

    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
    client.model = "test-model"
    client.base_url = "http://model-server:8000/v1"
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    assert list(client.stream_reply(user_text="hello", system_context="system")) == ["hel", "lo"]
    assert calls[0]["model"] == "test-model"
    assert calls[0]["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hello"},
    ]
    assert calls[0]["stream"] is True


def test_client_handles_empty_delta_content():
    class Completions:
        def create(self, **kwargs):
            return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))])])

    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
    client.model = "test-model"
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    assert list(client.stream_reply(user_text="hello")) == []


def test_client_lists_served_model_ids():
    class Models:
        def list(self):
            return SimpleNamespace(data=[
                SimpleNamespace(id="qwen-main"),
                SimpleNamespace(id="another-model"),
            ])

    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
    client._client = SimpleNamespace(models=Models())

    assert client.list_model_ids() == ["qwen-main", "another-model"]


def test_client_completes_a_short_non_streaming_message():
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[
                SimpleNamespace(message=SimpleNamespace(content="short reply"))
            ])

    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
    client.model = "test-model"
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    assert client.complete_messages(
        messages=[{"role": "user", "content": "hello"}], max_tokens=7
    ) == "short reply"
    assert calls[0]["stream"] is False
    assert calls[0]["max_tokens"] == 7


def test_client_warmup_requires_its_configured_model_to_be_served():
    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
    client.model = "expected-model"
    client.list_model_ids = lambda: ["some-other-model"]

    assert client.warmup() is False


def test_client_streams_the_completion_endpoint_when_configured():
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return iter([
                SimpleNamespace(choices=[SimpleNamespace(text="4")]),
            ])

    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
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


def test_client_folds_system_text_into_first_user_turn_for_templates_without_system_role():
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return iter([
                SimpleNamespace(choices=[
                    SimpleNamespace(delta=SimpleNamespace(content="reply"))
                ]),
            ])

    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
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


def test_client_rejects_invalid_request_contracts():
    for kwargs in (
        {"request_mode": "raw"},
        {"system_role_mode": "inline"},
        {"max_tokens": 0},
        {"dialogue_temperature": -1},
        {"dialogue_top_p": 1.5},
        {"dialogue_top_k": 0},
    ):
        base = {
            "model": "test-model",
            "base_url": "http://model-server:8000/v1",
        }
        base.update(kwargs)
        try:
            OpenAICompatibleClient(**base)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {kwargs}")


def test_request_body_carries_generation_options():
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return iter([])

    client = OpenAICompatibleClient(
        model="qwen-main",
        base_url="http://model-server:8000/v1",
        max_tokens=256,
        dialogue_temperature=0.35,
        dialogue_top_p=0.8,
        dialogue_top_k=20,
        dialogue_presence_penalty=1.5,
        dialogue_enable_thinking=False,
    )
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    list(client.stream_messages(messages=[{"role": "user", "content": "hello"}]))

    request = calls[0]
    assert request["temperature"] == 0.35
    assert request["top_p"] == 0.8
    assert request["presence_penalty"] == 1.5
    assert request["max_tokens"] == 256
    assert request["extra_body"]["top_k"] == 20
    assert request["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_request_body_omits_optional_extras_when_unconfigured():
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return iter([])

    client = OpenAICompatibleClient(
        model="qwen-main",
        base_url="http://model-server:8000/v1",
    )
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    list(client.stream_messages(messages=[{"role": "user", "content": "hello"}]))

    assert "extra_body" not in calls[0]
    assert calls[0]["temperature"] == 0.35
    assert calls[0]["top_p"] == 0.8
