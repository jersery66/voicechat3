"""The dialogue client factory builds from the runtime configuration."""

from deployment.runtime_config import load_runtime_config
from inference.factory import build_dialogue_client
from inference.openai_client import OpenAICompatibleClient

CONFIG_YAML = """dialogue:
  provider: openai_compatible
  base_url: http://127.0.0.1:8000/v1
  model: contract-model
  timeout: 90
  max_tokens: 256
  temperature: 0.7
  top_p: 0.9
  top_k: 20
  presence_penalty: 1.5
  enable_thinking: false
  request_mode: chat
  system_role_mode: native
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


def _write_config(tmp_path, text=CONFIG_YAML):
    path = tmp_path / "runtime.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_factory_transfers_the_full_generation_contract(tmp_path):
    client = build_dialogue_client(load_runtime_config(_write_config(tmp_path)))

    assert isinstance(client, OpenAICompatibleClient)
    assert client.model == "contract-model"
    assert client.base_url == "http://127.0.0.1:8000/v1"
    assert client.request_mode == "chat"
    assert client.system_role_mode == "native"
    assert client.max_tokens == 256
    assert client.dialogue_temperature == 0.7
    assert client.dialogue_top_p == 0.9
    assert client.dialogue_top_k == 20
    assert client.dialogue_presence_penalty == 1.5
    assert client.dialogue_enable_thinking is False


def test_factory_uses_the_shipped_runtime_config_by_default():
    client = build_dialogue_client()

    assert isinstance(client, OpenAICompatibleClient)
    assert client.model == "Qwen3.8-27B-Q4_K_S"
    assert client.base_url == "http://127.0.0.1:8000/v1"
    assert client.max_tokens == 1024
    assert client.dialogue_temperature == 0.35
    assert client.dialogue_enable_thinking is False


def test_factory_ignores_env_overrides_for_immutable_configs(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICECHAT_DIALOGUE_BASE_URL", "http://wrong.example/v1")
    monkeypatch.setenv("VOICECHAT_DIALOGUE_MODEL", "wrong-model")

    client = build_dialogue_client(load_runtime_config(_write_config(tmp_path)))

    assert client.base_url == "http://127.0.0.1:8000/v1"
    assert client.model == "contract-model"


def test_factory_honors_env_overrides_for_non_immutable_configs(tmp_path, monkeypatch):
    text = CONFIG_YAML.replace("immutable_contract: true", "immutable_contract: false")
    monkeypatch.setenv("VOICECHAT_DIALOGUE_BASE_URL", "http://dev-override.example/v1")

    client = build_dialogue_client(load_runtime_config(_write_config(tmp_path, text)))

    assert client.base_url == "http://dev-override.example/v1"


def test_factory_returns_none_for_the_ollama_provider(tmp_path):
    text = CONFIG_YAML.replace("provider: openai_compatible", "provider: ollama")

    assert build_dialogue_client(load_runtime_config(_write_config(tmp_path, text))) is None


def test_factory_supports_completion_request_mode(tmp_path):
    text = CONFIG_YAML.replace("request_mode: chat", "request_mode: completion")

    client = build_dialogue_client(load_runtime_config(_write_config(tmp_path, text)))

    assert client.request_mode == "completion"


def test_factory_uses_the_configured_timeout_and_accepts_an_override(tmp_path):
    client = build_dialogue_client(load_runtime_config(_write_config(tmp_path)))
    assert client._client.timeout == 90.0

    client = build_dialogue_client(load_runtime_config(_write_config(tmp_path)), timeout=5.0)
    assert client._client.timeout == 5.0


def test_factory_keeps_dialogue_and_agent_endpoints_independent(tmp_path):
    text = CONFIG_YAML.replace(
        "agent:\n  enabled: false",
        "agent:\n  enabled: true\n  base_url: http://127.0.0.1:8001/v1\n  model: contract-agent",
    )
    config = load_runtime_config(_write_config(tmp_path, text))

    client = build_dialogue_client(config)

    assert client.base_url == "http://127.0.0.1:8000/v1"
    assert client.model == "contract-model"
    assert config.agent.base_url == "http://127.0.0.1:8001/v1"
    assert config.agent.model == "contract-agent"
