"""Contract tests for the config-driven runtime configuration.

The runtime config replaces hardware-specific deployment profiles: one YAML
file selects every model, engine, endpoint, and generation parameter, and
unknown keys or invalid values must fail loudly instead of silently
selecting something else.
"""

import pytest

from deployment import runtime_config as rc
from deployment.runtime_config import (
    PROVIDER_OLLAMA,
    PROVIDER_OPENAI_COMPATIBLE,
    RuntimeConfigError,
    get_runtime_config,
    load_runtime_config,
    reset_runtime_config_cache,
)

MINIMAL_YAML = """
dialogue:
  provider: openai_compatible
  base_url: http://127.0.0.1:8000/v1
  model: test-model
agent:
  enabled: false
asr:
  provider: funasr
tts:
  provider: voxcpm
runtime:
  strict_preflight: false
  immutable_contract: false
"""


def _write(tmp_path, text, name="runtime.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_shipped_runtime_config_loads():
    config = load_runtime_config()
    assert config.dialogue.provider == PROVIDER_OPENAI_COMPATIBLE
    assert config.dialogue.base_url == "http://127.0.0.1:8000/v1"
    assert config.dialogue.model == "Qwen3.8-27B-Q4_K_S"
    assert config.dialogue.enable_thinking is False
    assert config.dialogue.max_tokens == 1024
    assert config.agent.enabled is True
    assert config.agent.model == "Qwen3-4B-Instruct-2507"
    assert config.agent.base_url == "http://127.0.0.1:8001/v1"
    assert config.asr.provider == "funasr"
    assert config.tts.provider == "voxcpm"
    assert config.runtime.strict_preflight is True
    assert config.runtime.immutable_contract is True
    assert config.runtime.manage_model_servers is False
    assert config.runtime.enable_streaming_tts is True
    assert config.dialogue_is_openai_compatible
    assert not config.dialogue_is_ollama
    assert config.source_path.endswith("runtime.yaml")


def test_missing_file_raises_with_path(tmp_path):
    missing = tmp_path / "nope.yaml"
    with pytest.raises(RuntimeConfigError) as exc:
        load_runtime_config(missing)
    assert str(missing) in str(exc.value)
    assert rc.RUNTIME_CONFIG_ENV_VAR in str(exc.value)


def test_invalid_yaml_raises(tmp_path):
    path = _write(tmp_path, "dialogue: [unclosed\n")
    with pytest.raises(RuntimeConfigError) as exc:
        load_runtime_config(path)
    assert "not valid YAML" in str(exc.value)


def test_non_mapping_top_level_raises(tmp_path):
    path = _write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(RuntimeConfigError) as exc:
        load_runtime_config(path)
    assert "mapping" in str(exc.value)


def test_missing_required_section_raises(tmp_path):
    path = _write(tmp_path, "dialogue:\n  provider: ollama\n  base_url: http://x/v1\n  model: m\n")
    with pytest.raises(RuntimeConfigError) as exc:
        load_runtime_config(path)
    assert "agent" in str(exc.value)


def test_unknown_top_level_section_raises(tmp_path):
    path = _write(tmp_path, MINIMAL_YAML + "hardware:\n  gpu: rtx5090\n")
    with pytest.raises(RuntimeConfigError) as exc:
        load_runtime_config(path)
    assert "hardware" in str(exc.value)


def test_unknown_dialogue_key_raises(tmp_path):
    path = _write(tmp_path, MINIMAL_YAML.replace("  model: test-model", "  model: test-model\n  vllm_gpu_memory_utilization: 0.9"))
    with pytest.raises(RuntimeConfigError) as exc:
        load_runtime_config(path)
    assert "vllm_gpu_memory_utilization" in str(exc.value)


def test_unknown_runtime_key_raises(tmp_path):
    text = MINIMAL_YAML.replace(
        "  strict_preflight: false",
        "  strict_preflight: false\n  expected_gpu_memory_gb: 32",
    )
    path = _write(tmp_path, text)
    with pytest.raises(RuntimeConfigError) as exc:
        load_runtime_config(path)
    assert "expected_gpu_memory_gb" in str(exc.value)


def test_unknown_provider_raises(tmp_path):
    path = _write(tmp_path, MINIMAL_YAML.replace("provider: openai_compatible", "provider: tensorrt"))
    with pytest.raises(RuntimeConfigError) as exc:
        load_runtime_config(path)
    assert "tensorrt" in str(exc.value)


@pytest.mark.parametrize(
    "replacement, message",
    [
        ("  max_tokens: 0", "max_tokens"),
        ("  temperature: -0.5", "temperature"),
        ("  top_p: 1.5", "top_p"),
        ("  top_p: 0", "top_p"),
        ("  top_k: 0", "top_k"),
        ("  timeout: 0", "timeout"),
        ("  request_mode: raw", "request_mode"),
        ("  system_role_mode: inline", "system_role_mode"),
        ("  base_url: localhost:8000", "base_url"),
    ],
)
def test_dialogue_range_validation(tmp_path, replacement, message):
    path = _write(tmp_path, MINIMAL_YAML.replace("  model: test-model", "  model: test-model\n" + replacement))
    with pytest.raises(RuntimeConfigError) as exc:
        load_runtime_config(path)
    assert message in str(exc.value)


def test_dialogue_requires_model_and_base_url(tmp_path):
    text = """dialogue:
  provider: ollama
agent:
  enabled: false
asr:
  provider: funasr
tts:
  provider: voxcpm
runtime: {}
"""
    with pytest.raises(RuntimeConfigError) as exc:
        load_runtime_config(_write(tmp_path, text))
    assert "base_url" in str(exc.value)


def test_agent_enabled_requires_endpoint_and_model(tmp_path):
    text = MINIMAL_YAML.replace("  enabled: false", "  enabled: true")
    path = _write(tmp_path, text)
    with pytest.raises(RuntimeConfigError) as exc:
        load_runtime_config(path)
    assert "agent.base_url" in str(exc.value)


def test_agent_disabled_allows_empty_endpoint(tmp_path):
    config = load_runtime_config(_write(tmp_path, MINIMAL_YAML))
    assert config.agent.enabled is False
    assert config.agent.base_url == ""
    assert config.agent.model == ""


def test_agent_independent_of_dialogue(tmp_path):
    text = MINIMAL_YAML.replace(
        "agent:\n  enabled: false",
        "agent:\n  enabled: true\n  provider: ollama\n  base_url: http://127.0.0.1:11434\n  model: qwen2.5:3b",
    )
    config = load_runtime_config(_write(tmp_path, text))
    assert config.dialogue.provider == PROVIDER_OPENAI_COMPATIBLE
    assert config.agent.provider == PROVIDER_OLLAMA
    assert config.agent_is_ollama
    assert not config.agent_is_openai_compatible
    assert config.agent.base_url == "http://127.0.0.1:11434"


def test_ollama_dialogue_provider(tmp_path):
    text = MINIMAL_YAML.replace("provider: openai_compatible", "provider: ollama")
    config = load_runtime_config(_write(tmp_path, text))
    assert config.dialogue_is_ollama
    assert not config.dialogue_is_openai_compatible


def test_model_path_auto_normalizes_to_none(tmp_path):
    config = load_runtime_config(_write(tmp_path, MINIMAL_YAML))
    assert config.asr.model_path is None
    assert config.tts.model_path is None
    assert config.tts.voice_prompt_path is None

    text = MINIMAL_YAML.replace("  provider: funasr", "  provider: funasr\n  model_path: auto")
    config = load_runtime_config(_write(tmp_path, text, "auto.yaml"))
    assert config.asr.model_path is None

    text = MINIMAL_YAML.replace("  provider: funasr", "  provider: funasr\n  model_path: D:/models/funasr")
    config = load_runtime_config(_write(tmp_path, text, "abs.yaml"))
    assert config.asr.model_path == "D:/models/funasr"


def test_env_var_selects_config_file(tmp_path, monkeypatch):
    path = _write(tmp_path, MINIMAL_YAML.replace("model: test-model", "model: env-model"), "alt.yaml")
    monkeypatch.setenv(rc.RUNTIME_CONFIG_ENV_VAR, str(path))
    reset_runtime_config_cache()
    try:
        config = get_runtime_config()
        assert config.dialogue.model == "env-model"
        assert config.source_path == str(path)
    finally:
        monkeypatch.delenv(rc.RUNTIME_CONFIG_ENV_VAR)
        reset_runtime_config_cache()


def test_immutable_contract_ignores_env_overrides(tmp_path):
    text = MINIMAL_YAML.replace("immutable_contract: false", "immutable_contract: true")
    path = _write(tmp_path, text)
    env = {
        "VOICECHAT_DIALOGUE_BASE_URL": "http://127.0.0.1:9999/v1",
        "VOICECHAT_DIALOGUE_MODEL": "hijacked",
        "VOICECHAT_AGENT_BASE_URL": "http://127.0.0.1:8888/v1",
        "AGENT_MODEL": "hijacked-agent",
    }
    config = load_runtime_config(path, environment=env)
    assert config.dialogue.base_url == "http://127.0.0.1:8000/v1"
    assert config.dialogue.model == "test-model"


def test_non_immutable_contract_honors_env_overrides(tmp_path):
    text = MINIMAL_YAML.replace(
        "agent:\n  enabled: false",
        "agent:\n  enabled: true\n  base_url: http://127.0.0.1:8001/v1\n  model: base-agent",
    )
    path = _write(tmp_path, text)
    env = {
        "VOICECHAT_DIALOGUE_BASE_URL": "http://127.0.0.1:9999/v1",
        "VOICECHAT_DIALOGUE_MODEL": "override-model",
        "VOICECHAT_AGENT_BASE_URL": "http://127.0.0.1:8888/v1",
        "AGENT_MODEL": "override-agent",
    }
    config = load_runtime_config(path, environment=env)
    assert config.dialogue.base_url == "http://127.0.0.1:9999/v1"
    assert config.dialogue.model == "override-model"
    assert config.agent.base_url == "http://127.0.0.1:8888/v1"
    assert config.agent.model == "override-agent"


def test_disabled_agent_ignores_env_overrides(tmp_path):
    path = _write(tmp_path, MINIMAL_YAML)
    env = {"VOICECHAT_AGENT_BASE_URL": "http://127.0.0.1:8888/v1", "AGENT_MODEL": "override-agent"}
    config = load_runtime_config(path, environment=env)
    assert config.agent.enabled is False
    assert config.agent.base_url == ""
    assert config.agent.model == ""


def test_legacy_env_model_aliases_honored(tmp_path):
    path = _write(tmp_path, MINIMAL_YAML)
    env = {"VOICECHAT_VLLM_MODEL": "legacy-vllm-model"}
    config = load_runtime_config(path, environment=env)
    assert config.dialogue.model == "legacy-vllm-model"
    env = {"OLLAMA_MODEL": "legacy-ollama-model"}
    config = load_runtime_config(path, environment=env)
    assert config.dialogue.model == "legacy-ollama-model"


def test_get_runtime_config_caches(tmp_path, monkeypatch):
    path = _write(tmp_path, MINIMAL_YAML)
    monkeypatch.setenv(rc.RUNTIME_CONFIG_ENV_VAR, str(path))
    reset_runtime_config_cache()
    try:
        first = get_runtime_config()
        assert get_runtime_config() is first
        reset_runtime_config_cache()
        assert get_runtime_config() is not first
    finally:
        monkeypatch.delenv(rc.RUNTIME_CONFIG_ENV_VAR)
        reset_runtime_config_cache()


def test_summary_reports_effective_configuration():
    config = load_runtime_config()
    summary = config.summary()
    assert summary["dialogue"]["model"] == "Qwen3.8-27B-Q4_K_S"
    assert summary["agent"]["model"] == "Qwen3-4B-Instruct-2507"
    assert summary["runtime"]["strict_preflight"] is True
    assert summary["asr"]["provider"] == "funasr"


def test_typed_fields_are_coerced(tmp_path):
    text = MINIMAL_YAML.replace("  model: test-model", "  model: test-model\n  timeout: 60\n  temperature: 0\n  presence_penalty: 1.5")
    config = load_runtime_config(_write(tmp_path, text))
    assert isinstance(config.dialogue.timeout, float)
    assert config.dialogue.timeout == 60.0
    assert config.dialogue.temperature == 0.0
    assert config.dialogue.presence_penalty == 1.5


def test_bool_field_type_is_enforced(tmp_path):
    text = MINIMAL_YAML.replace("strict_preflight: false", 'strict_preflight: "false"')
    with pytest.raises(RuntimeConfigError) as exc:
        load_runtime_config(_write(tmp_path, text))
    assert "strict_preflight" in str(exc.value)
