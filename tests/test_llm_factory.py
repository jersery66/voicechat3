"""The existing LLM service selects vLLM only through an explicit profile."""

from services.llm_factory import build_llm_service


def test_a100_profile_builds_a_vllm_backed_compatibility_service(monkeypatch):
    captured = {}

    class FakeVLLMClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.model = kwargs["model"]

        def list_model_ids(self):
            return [self.model]

    monkeypatch.setattr("services.llm_factory.VLLMOpenAIClient", FakeVLLMClient)

    service = build_llm_service(profile_name="a100_80g")

    assert service.model == "Qwen/Qwen2.5-72B-Instruct-AWQ"
    assert captured["base_url"] == "http://127.0.0.1:8000/v1"
    assert service.test_connection() is True


def test_a100_llm_factory_ignores_stale_dialogue_endpoint_override(monkeypatch):
    captured = {}

    class FakeVLLMClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("VOICECHAT_DIALOGUE_BASE_URL", "http://stale-host:9000/v1")
    monkeypatch.setattr("services.llm_factory.VLLMOpenAIClient", FakeVLLMClient)

    build_llm_service(profile_name="a100_80g")

    assert captured["base_url"] == "http://127.0.0.1:8000/v1"


def test_local_vllm_profile_selects_completion_transport_and_small_output_budget(monkeypatch):
    captured = {}

    class FakeVLLMClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("services.llm_factory.VLLMOpenAIClient", FakeVLLMClient)

    service = build_llm_service(profile_name="dev_vllm_6g")

    assert service.model == "gemma-2b-awq"
    assert captured["request_mode"] == "completion"
    assert captured["system_role_mode"] == "native"
    assert captured["max_tokens"] == 96
    assert service.system_prompt == "Reply briefly and directly to the user."


def test_vllm_compatibility_service_preserves_clean_history_and_short_reply():
    observed_messages = []
    observed_short_messages = []

    class Backend:
        def stream_messages(self, *, messages):
            observed_messages.append(messages)
            yield "analysis|||spoken"

        def complete_messages(self, *, messages, max_tokens):
            observed_short_messages.append((messages, max_tokens))
            return "short reply"

    service = build_llm_service.__globals__["VLLMCompatibleLLMService"](Backend(), model="test")

    assert list(service.chat("hello")) == ["analysis|||spoken"]
    assert service.conversation_history[-1] == {"role": "assistant", "content": "spoken"}
    assert service.generate_short_text("brief") == "short reply"
    assert observed_messages[0][-1:] == [
        {"role": "user", "content": "hello"},
    ]
    assert observed_short_messages == [(
        [{"role": "user", "content": "brief"}],
        60,
    )]


def test_vllm_compatibility_service_bounds_history_to_recent_turns():
    observed_messages = []

    class Backend:
        def stream_messages(self, *, messages):
            observed_messages.append(messages)
            yield "analysis|||spoken"

    service = build_llm_service.__globals__["VLLMCompatibleLLMService"](Backend(), model="test")
    for index in range(service.MAX_HISTORY_TURNS + 2):
        assert list(service.chat(f"turn {index}")) == ["analysis|||spoken"]

    assert len(service.conversation_history) == service.MAX_HISTORY_TURNS * 2
    # One system message plus no more than 20 dialogue turns, including the
    # current user message.
    assert len(observed_messages[-1]) <= 1 + service.MAX_HISTORY_TURNS * 2


def test_vllm_compatibility_service_uses_the_standard_counselling_system_prompt():
    from config import SYSTEM_PROMPT

    observed_messages = []

    class Backend:
        def stream_messages(self, *, messages):
            observed_messages.append(messages)
            yield "analysis|||spoken"

    service = build_llm_service.__globals__["VLLMCompatibleLLMService"](
        Backend(), model="test"
    )

    assert list(service.chat("hello")) == ["analysis|||spoken"]
    assert observed_messages[0][0] == {"role": "system", "content": SYSTEM_PROMPT}


def test_vllm_compatibility_service_rejects_an_unserved_dialogue_model():
    class Backend:
        def list_model_ids(self):
            return ["some-other-model"]

    service = build_llm_service.__globals__["VLLMCompatibleLLMService"](
        Backend(), model="expected-model"
    )

    assert service.test_connection() is False


def test_vllm_short_text_generation_does_not_add_a_dialogue_turn():
    captured = {}

    class Backend:
        def stream_messages(self, *, messages):
            yield "wrong|||conversation reply"

        def complete_messages(self, *, messages, max_tokens):
            captured["messages"] = messages
            captured["max_tokens"] = max_tokens
            return "short reply"

    service = build_llm_service.__globals__["VLLMCompatibleLLMService"](
        Backend(), model="test"
    )

    assert service.generate_short_text("say hello", max_tokens=7) == "short reply"
    assert captured == {
        "messages": [{"role": "user", "content": "say hello"}],
        "max_tokens": 7,
    }
    assert service.conversation_history == []


def test_vllm_warmup_delegates_to_the_selected_backend():
    calls = []

    class Backend:
        def warmup(self):
            calls.append("warmup")
            return True

    service = build_llm_service.__globals__["VLLMCompatibleLLMService"](
        Backend(), model="test"
    )

    assert service.warmup() is True
    assert calls == ["warmup"]


def test_legacy_singleton_resolves_through_the_profile_factory(monkeypatch):
    import services.llm_service as llm_module

    expected = object()
    llm_module._llm_service = None
    monkeypatch.setattr("services.llm_factory.build_llm_service", lambda: expected)

    assert llm_module.get_llm_service() is expected
