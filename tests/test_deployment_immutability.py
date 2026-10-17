"""Deployment immutability and preflight policy belong to configuration data."""

from types import SimpleNamespace

from deployment.runtime_config import load_runtime_config
from scripts import check_config
from services import llm_factory

CONFIG_YAML = """dialogue:
  provider: openai_compatible
  base_url: http://127.0.0.1:8000/v1
  model: contract-model
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


def _write_config(tmp_path):
    path = tmp_path / "runtime.yaml"
    path.write_text(CONFIG_YAML, encoding="utf-8")
    return path


def test_shipped_runtime_config_pins_immutability_and_preflight():
    config = load_runtime_config()

    assert config.runtime.immutable_contract is True
    assert config.runtime.strict_preflight is True


def test_configuration_may_explicitly_relax_the_policy(tmp_path):
    path = tmp_path / "runtime.yaml"
    path.write_text(
        CONFIG_YAML.replace("strict_preflight: true", "strict_preflight: false")
        .replace("immutable_contract: true", "immutable_contract: false"),
        encoding="utf-8",
    )
    config = load_runtime_config(path)

    assert config.runtime.immutable_contract is False
    assert config.runtime.strict_preflight is False


def test_immutable_runtime_config_pins_the_compatibility_service(tmp_path, monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("inference.factory.OpenAICompatibleClient", FakeClient)
    monkeypatch.setenv("VOICECHAT_DIALOGUE_BASE_URL", "http://wrong.example/v1")
    monkeypatch.setenv("VOICECHAT_DIALOGUE_MODEL", "wrong-dialogue")
    monkeypatch.setenv("AGENT_MODEL", "wrong-agent")

    llm_factory.build_llm_service(load_runtime_config(_write_config(tmp_path)))

    assert captured["base_url"] == "http://127.0.0.1:8000/v1"
    assert captured["model"] == "contract-model"


def test_non_immutable_runtime_config_honors_endpoint_overrides(tmp_path, monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    path = tmp_path / "runtime.yaml"
    path.write_text(
        CONFIG_YAML.replace("immutable_contract: true", "immutable_contract: false"),
        encoding="utf-8",
    )
    monkeypatch.setattr("inference.factory.OpenAICompatibleClient", FakeClient)
    monkeypatch.setenv("VOICECHAT_DIALOGUE_BASE_URL", "http://dev-override.example/v1")

    llm_factory.build_llm_service(load_runtime_config(path))

    assert captured["base_url"] == "http://dev-override.example/v1"


def test_check_config_uses_runtime_owned_strict_preflight(monkeypatch):
    monkeypatch.setattr(check_config, "_check_openai_compatible_model", lambda *_: False)
    monkeypatch.setattr(check_config, "get_runtime_config", lambda: SimpleNamespace(
        runtime=SimpleNamespace(strict_preflight=True),
    ))
    monkeypatch.setattr("config.AGENT_PROVIDER", "openai_compatible")
    monkeypatch.setattr("config.AGENT_MODEL", "agent-model")
    monkeypatch.setattr("config.AGENT_MODEL_SERVER", "http://agent:8001/v1")

    assert check_config.check_agent_backend() is False
