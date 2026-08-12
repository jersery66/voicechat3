"""Report fallbacks must not bypass vLLM with an Ollama-only client."""

from services.report_service import ReportService


def test_report_stream_fallback_uses_the_compatibility_chat_surface(monkeypatch):
    class Backend:
        def chat(self, prompt):
            yield "first"
            yield "second"

    service = ReportService(llm_service=Backend())
    monkeypatch.setattr("services.report_service.DIALOGUE_BACKEND", "vllm")

    assert list(service._fallback_chat("prompt", stream=True)) == ["first", "second"]
