"""The compatibility LLM service is selected by the runtime configuration."""

from deployment.runtime_config import load_runtime_config
from services.llm_factory import OpenAICompatibleLLMService, build_llm_service

CONFIG_YAML = """dialogue:
  provider: openai_compatible
  base_url: http://127.0.0.1:8000/v1
  model: contract-model
  max_tokens: 256
  temperature: 0.7
  top_p: 0.9
agent:
  enabled: false
asr:
  provider: funasr
tts:
  provider: voxcpm
runtime:
  strict_preflight: true
  immutable_contract: true
"""


class _FakeClient:
    def __init__(self, **kwargs):
        self.model = kwargs["model"]

    def list_model_ids(self):
        return [self.model]


def _write_config(tmp_path, text=CONFIG_YAML):
    path = tmp_path / "runtime.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_openai_compatible_provider_builds_the_compatibility_service(tmp_path, monkeypatch):
    captured = {}

    class FakeClient(_FakeClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            captured.update(kwargs)

    monkeypatch.setattr("inference.factory.OpenAICompatibleClient", FakeClient)

    service = build_llm_service(load_runtime_config(_write_config(tmp_path)))

    assert isinstance(service, OpenAICompatibleLLMService)
    assert service.model == "contract-model"
    assert captured["base_url"] == "http://127.0.0.1:8000/v1"
    assert captured["max_tokens"] == 256
    assert captured["dialogue_temperature"] == 0.7
    assert captured["dialogue_top_p"] == 0.9
    assert service.test_connection() is True


def test_default_service_uses_the_shipped_runtime_config(monkeypatch):
    monkeypatch.setattr("inference.factory.OpenAICompatibleClient", _FakeClient)

    service = build_llm_service()

    assert isinstance(service, OpenAICompatibleLLMService)
    assert service.model == "Qwen3.8-27B-Q4_K_S"


def test_system_prompt_override_is_applied(tmp_path, monkeypatch):
    text = CONFIG_YAML.replace(
        "  model: contract-model",
        '  model: contract-model\n  system_prompt_override: "Reply briefly and directly."',
    )
    monkeypatch.setattr("inference.factory.OpenAICompatibleClient", _FakeClient)

    service = build_llm_service(load_runtime_config(_write_config(tmp_path, text)))

    assert service.system_prompt == "Reply briefly and directly."


def test_ollama_provider_builds_the_native_service(tmp_path, monkeypatch):
    text = CONFIG_YAML.replace("provider: openai_compatible", "provider: ollama")
    captured = {}

    class FakeLLMService:
        def __init__(self, *, model, host):
            captured["model"] = model
            captured["host"] = host

    monkeypatch.setattr("services.llm_service.LLMService", FakeLLMService)

    service = build_llm_service(load_runtime_config(_write_config(tmp_path, text)))

    assert isinstance(service, FakeLLMService)
    assert captured == {"model": "contract-model", "host": "http://127.0.0.1:8000/v1"}


def test_compatibility_service_preserves_clean_history_and_short_reply():
    observed_messages = []
    observed_short_messages = []

    class Backend:
        def stream_messages(self, *, messages):
            observed_messages.append(messages)
            yield "analysis|||spoken"

        def complete_messages(self, *, messages, max_tokens):
            observed_short_messages.append((messages, max_tokens))
            return "short reply"

    service = OpenAICompatibleLLMService(Backend(), model="test")

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


def test_compatibility_service_bounds_history_to_recent_turns():
    observed_messages = []

    class Backend:
        def stream_messages(self, *, messages):
            observed_messages.append(messages)
            yield "analysis|||spoken"

    service = OpenAICompatibleLLMService(Backend(), model="test")
    for index in range(service.MAX_HISTORY_TURNS + 2):
        assert list(service.chat(f"turn {index}")) == ["analysis|||spoken"]

    assert len(service.conversation_history) == service.MAX_HISTORY_TURNS * 2
    # One system message plus no more than 20 dialogue turns, including the
    # current user message.
    assert len(observed_messages[-1]) <= 1 + service.MAX_HISTORY_TURNS * 2


def test_compatibility_service_uses_the_standard_counselling_system_prompt():
    from config import SYSTEM_PROMPT

    observed_messages = []

    class Backend:
        def stream_messages(self, *, messages):
            observed_messages.append(messages)
            yield "analysis|||spoken"

    service = OpenAICompatibleLLMService(Backend(), model="test")

    assert list(service.chat("hello")) == ["analysis|||spoken"]
    assert observed_messages[0][0] == {"role": "system", "content": SYSTEM_PROMPT}


def test_compatibility_service_rejects_an_unserved_dialogue_model():
    class Backend:
        def list_model_ids(self):
            return ["some-other-model"]

    service = OpenAICompatibleLLMService(Backend(), model="expected-model")

    assert service.test_connection() is False


def test_short_text_generation_does_not_add_a_dialogue_turn():
    captured = {}

    class Backend:
        def stream_messages(self, *, messages):
            yield "wrong|||conversation reply"

        def complete_messages(self, *, messages, max_tokens):
            captured["messages"] = messages
            captured["max_tokens"] = max_tokens
            return "short reply"

    service = OpenAICompatibleLLMService(Backend(), model="test")

    assert service.generate_short_text("say hello", max_tokens=7) == "short reply"
    assert captured == {
        "messages": [{"role": "user", "content": "say hello"}],
        "max_tokens": 7,
    }
    assert service.conversation_history == []


def test_warmup_delegates_to_the_selected_backend():
    calls = []

    class Backend:
        def warmup(self):
            calls.append("warmup")
            return True

    service = OpenAICompatibleLLMService(Backend(), model="test")

    assert service.warmup() is True
    assert calls == ["warmup"]


def test_legacy_singleton_resolves_through_the_factory(monkeypatch):
    import services.llm_service as llm_module

    expected = object()
    llm_module._llm_service = None
    monkeypatch.setattr("services.llm_factory.build_llm_service", lambda: expected)

    assert llm_module.get_llm_service() is expected
