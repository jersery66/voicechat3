"""The existing LLM service selects vLLM only through an explicit profile."""

from services.llm_factory import build_llm_service


def test_a100_profile_builds_a_vllm_backed_compatibility_service(monkeypatch):
    captured = {}

    class FakeVLLMClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("services.llm_factory.VLLMOpenAIClient", FakeVLLMClient)

    service = build_llm_service(profile_name="a100_80g")

    assert service.model == "Qwen/Qwen2.5-72B-Instruct-AWQ"
    assert captured["base_url"] == "http://127.0.0.1:8000/v1"
    assert service.test_connection() is True


def test_vllm_compatibility_service_preserves_clean_history_and_short_reply():
    observed_messages = []

    class Backend:
        def stream_messages(self, *, messages):
            observed_messages.append(messages)
            yield "analysis|||spoken"

    service = build_llm_service.__globals__["VLLMCompatibleLLMService"](Backend(), model="test")

    assert list(service.chat("hello")) == ["analysis|||spoken"]
    assert service.conversation_history[-1] == {"role": "assistant", "content": "spoken"}
    assert service.generate_short_text("brief") == "analysis|||spoken"
    assert observed_messages[1][-3:] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "spoken"},
        {"role": "user", "content": "brief"},
    ]


def test_legacy_singleton_resolves_through_the_profile_factory(monkeypatch):
    import services.llm_service as llm_module

    expected = object()
    llm_module._llm_service = None
    monkeypatch.setattr("services.llm_factory.build_llm_service", lambda: expected)

    assert llm_module.get_llm_service() is expected
