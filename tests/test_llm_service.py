"""Regression tests for LLMService streaming edge cases."""

from services.llm_service import LLMService


class _EmptyStreamThenReplyClient:
    """Returns no stream chunks, then a valid non-streaming retry reply."""

    def chat(self, **kwargs):
        if kwargs["stream"]:
            return iter(())
        return {"message": {"content": "analysis|||recovered spoken"}}


def _service_with(client):
    service = LLMService.__new__(LLMService)
    service.client = client
    service.model = "test-model"
    service.system_prompt = "system"
    service.history_context = ""
    service.conversation_history = []
    service._maybe_summarize = lambda: None
    return service


def test_empty_stream_retry_yields_recovered_reply_to_caller():
    service = _service_with(_EmptyStreamThenReplyClient())

    assert list(service.chat("hello")) == ["analysis|||recovered spoken"]
    assert service.conversation_history[-1]["content"] == "recovered spoken"


def test_generate_short_text_reuses_service_host(monkeypatch):
    service = _service_with(_EmptyStreamThenReplyClient())
    service.host = "http://example.invalid:11434"
    captured = {}

    class Client:
        def chat(self, **kwargs):
            return {"message": {"content": "short reply"}}

    def fake_get_client(host):
        captured["host"] = host
        return Client()

    monkeypatch.setattr("services.llm_service.get_ollama_client", fake_get_client)

    assert service.generate_short_text("hello") == "short reply"
    assert captured["host"] == service.host
