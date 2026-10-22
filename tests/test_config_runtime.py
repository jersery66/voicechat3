"""config.py exposes the runtime configuration selected by runtime.yaml.

The runtime config (default config/runtime.yaml, override path with
VOICECHAT_RUNTIME_CONFIG) is the single source of truth for models,
engines, and endpoints. The application never inspects the GPU to choose
a model, so switching hardware or inference engines is a config edit.
"""

import importlib
import sys

from deployment import runtime_config as rc

MINIMAL_YAML = """dialogue:
  provider: openai_compatible
  base_url: http://127.0.0.1:9000/v1
  model: minimal-dialogue
agent:
  enabled: true
  provider: openai_compatible
  base_url: http://127.0.0.1:9001/v1
  model: minimal-agent
asr:
  provider: funasr
tts:
  provider: voxcpm
runtime:
  strict_preflight: false
  immutable_contract: false
"""


def _load_config_with(monkeypatch, tmp_path, text):
    """Import config with VOICECHAT_RUNTIME_CONFIG pointed at a temp file."""
    path = tmp_path / "runtime.yaml"
    path.write_text(text, encoding="utf-8")
    original = sys.modules.get("config")
    monkeypatch.setenv(rc.RUNTIME_CONFIG_ENV_VAR, str(path))
    rc.reset_runtime_config_cache()
    sys.modules.pop("config", None)
    try:
        return importlib.import_module("config")
    finally:
        sys.modules.pop("config", None)
        rc.reset_runtime_config_cache()
        monkeypatch.delenv(rc.RUNTIME_CONFIG_ENV_VAR, raising=False)
        if original is not None:
            sys.modules["config"] = original


def test_shipped_runtime_config_drives_config_module():
    import config

    assert config.DIALOGUE_PROVIDER == "openai_compatible"
    assert config.DIALOGUE_MODEL == "Qwen3.8-27B-Q4_K_S"
    assert config.DIALOGUE_BASE_URL == "http://127.0.0.1:8000/v1"
    assert config.AGENT_ENABLED is True
    assert config.AGENT_MODEL == "Qwen3-4B-Instruct-2507"
    assert config.AGENT_MODEL_SERVER == "http://127.0.0.1:8001/v1"
    assert config.ASR_PROVIDER == "funasr"
    assert config.TTS_PROVIDER == "voxcpm"
    assert config.RUNTIME_STRICT_PREFLIGHT is True
    assert config.RUNTIME_CONFIG.summary()["dialogue"]["model"] == "Qwen3.8-27B-Q4_K_S"
    # Guard/crisis controls are intentionally not exported.
    assert not hasattr(config, "GUARD_MODEL")
    assert not hasattr(config, "GUARD_MODEL_SERVER")


def test_alternate_runtime_config_selects_models(monkeypatch, tmp_path):
    config = _load_config_with(monkeypatch, tmp_path, MINIMAL_YAML)

    assert config.DIALOGUE_MODEL == "minimal-dialogue"
    assert config.AGENT_MODEL == "minimal-agent"
    assert config.DIALOGUE_BASE_URL == "http://127.0.0.1:9000/v1"
    assert config.AGENT_MODEL_SERVER == "http://127.0.0.1:9001/v1"


def test_immutable_contract_ignores_endpoint_overrides(monkeypatch, tmp_path):
    text = MINIMAL_YAML.replace("immutable_contract: false", "immutable_contract: true")
    monkeypatch.setenv("VOICECHAT_DIALOGUE_BASE_URL", "http://stale-host:9000/v1")
    monkeypatch.setenv("VOICECHAT_AGENT_BASE_URL", "http://stale-host:9001/v1")
    monkeypatch.setenv("VOICECHAT_DIALOGUE_MODEL", "stale-model")

    config = _load_config_with(monkeypatch, tmp_path, text)

    assert config.DIALOGUE_BASE_URL == "http://127.0.0.1:9000/v1"
    assert config.AGENT_MODEL_SERVER == "http://127.0.0.1:9001/v1"
    assert config.DIALOGUE_MODEL == "minimal-dialogue"


def test_non_immutable_contract_honors_endpoint_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICECHAT_DIALOGUE_BASE_URL", "http://stale-host:9000/v1")
    monkeypatch.setenv("VOICECHAT_DIALOGUE_MODEL", "override-model")

    config = _load_config_with(monkeypatch, tmp_path, MINIMAL_YAML)

    assert config.DIALOGUE_BASE_URL == "http://stale-host:9000/v1"
    assert config.DIALOGUE_MODEL == "override-model"


def test_ollama_provider_config(monkeypatch, tmp_path):
    text = MINIMAL_YAML.replace("provider: openai_compatible", "provider: ollama")
    config = _load_config_with(monkeypatch, tmp_path, text)

    assert config.DIALOGUE_PROVIDER == "ollama"
    assert config.DIALOGUE_BASE_URL == "http://127.0.0.1:9000/v1"


def test_agent_disabled_by_runtime_config(monkeypatch, tmp_path):
    text = MINIMAL_YAML.replace("  enabled: true", "  enabled: false")
    config = _load_config_with(monkeypatch, tmp_path, text)

    assert config.AGENT_ENABLED is False


def test_runtime_model_path_overrides_auto_detection(monkeypatch, tmp_path):
    text = MINIMAL_YAML.replace(
        "  provider: funasr", "  provider: funasr\n  model_path: D:/models/funasr"
    )
    config = _load_config_with(monkeypatch, tmp_path, text)

    assert config.FUNASR_MODEL_PATH == "D:/models/funasr"


def test_runtime_relative_model_path_resolves_against_models_root(monkeypatch, tmp_path):
    text = MINIMAL_YAML.replace(
        "  provider: funasr", "  provider: funasr\n  model_path: funasr/nano"
    )
    config = _load_config_with(monkeypatch, tmp_path, text)

    assert "funasr" in config.FUNASR_MODEL_PATH
    assert "nano" in config.FUNASR_MODEL_PATH


def test_models_root_sits_beside_the_application_directory(monkeypatch, tmp_path):
    """The bundle layout is <bundle>/app plus <bundle>/models.

    A plain checkout must be treated the same way, so the default models root
    is the parent of the application directory, not its grandparent. Reading
    the grandparent silently missed every model and disabled STT.
    """
    import os

    monkeypatch.delenv("VOICECHAT_MODELS_DIR", raising=False)
    config = _load_config_with(monkeypatch, tmp_path, MINIMAL_YAML)

    assert config.PROGRAM_ROOT == os.path.dirname(config.APP_ROOT)
    assert config.OFFLINE_MODELS_ROOT == os.path.join(config.PROGRAM_ROOT, "models")
