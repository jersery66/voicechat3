"""Report fallbacks must not bypass the dialogue service with an Ollama-only client."""

from services.report_service import ReportService


def test_report_stream_fallback_uses_the_compatibility_chat_surface(monkeypatch):
    class Backend:
        def chat(self, prompt):
            yield "first"
            yield "second"

    service = ReportService(llm_service=Backend())
    monkeypatch.setattr("services.report_service.DIALOGUE_PROVIDER", "openai_compatible")

    assert list(service._fallback_chat("prompt", stream=True)) == ["first", "second"]


def test_report_fallback_uses_the_ollama_surface_for_the_ollama_provider(monkeypatch):
    class Backend:
        def chat(self, **kwargs):
            assert kwargs["model"] == "dialogue-model"
            assert kwargs["stream"] is True
            yield "chunk"

    service = ReportService(llm_service=Backend())
    monkeypatch.setattr("services.report_service.DIALOGUE_PROVIDER", "ollama")
    monkeypatch.setattr("services.report_service.DIALOGUE_MODEL", "dialogue-model")

    assert list(service._fallback_chat("prompt", stream=True)) == ["chunk"]
