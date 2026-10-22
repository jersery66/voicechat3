"""Compatibility contract for the opt-in Qwen3.8 dialogue configuration.

The candidate is a runtime configuration file, not a hardware profile: the
same application serves any model the configuration names explicitly, and
an immutable contract keeps environment overrides out of experiments.
"""

from types import SimpleNamespace

import pytest

from deployment.runtime_config import RuntimeConfigError, load_runtime_config
from inference.factory import build_dialogue_client
from inference.openai_client import OpenAICompatibleClient

CANDIDATE_CONFIG_YAML = """dialogue:
  provider: openai_compatible
  base_url: http://127.0.0.1:8000/v1
  model: Qwen/Qwen3.8-27B-FP8
  max_tokens: 1024
  temperature: 0.7
  top_p: 0.8
  top_k: 20
  presence_penalty: 1.5
  enable_thinking: false
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
    path.write_text(CANDIDATE_CONFIG_YAML, encoding="utf-8")
    return path


def test_q38_01_production_configuration_names_its_dialogue_model_explicitly():
    config = load_runtime_config()
    assert config.dialogue.model
    assert config.dialogue.provider == "openai_compatible"


def test_q38_02_candidate_configuration_selects_qwen38_fp8(tmp_path):
    config = load_runtime_config(_write_config(tmp_path))
    assert config.dialogue.model == "Qwen/Qwen3.8-27B-FP8"
    assert config.dialogue.enable_thinking is False


def test_q38_03_candidate_configures_its_agent_independently(tmp_path):
    config = load_runtime_config(_write_config(tmp_path))
    assert config.agent.enabled is False
    assert config.dialogue.model == "Qwen/Qwen3.8-27B-FP8"


def test_q38_04_configuration_is_explicit_and_never_defaulted(tmp_path):
    with pytest.raises(RuntimeConfigError):
        load_runtime_config(tmp_path / "missing.yaml")


def test_q38_05_candidate_ignores_unauthorized_model_override(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICECHAT_DIALOGUE_MODEL", "unauthorized-model")
    monkeypatch.setenv("OLLAMA_MODEL", "stale-model")
    monkeypatch.setenv("AGENT_MODEL", "unauthorized-agent")
    config = load_runtime_config(_write_config(tmp_path))
    assert config.dialogue.model == "Qwen/Qwen3.8-27B-FP8"
    assert config.agent.enabled is False


def _fake_chat_client(calls, chunks=None, completion_text="reply"):
    chunks = chunks or [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"))])]

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if kwargs.get("stream"):
                return iter(chunks)
            return SimpleNamespace(choices=[
                SimpleNamespace(message=SimpleNamespace(content=completion_text))
            ])

    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
    client.model = "Qwen/Qwen3.8-27B-FP8"
    client.request_mode = "chat"
    client.system_role_mode = "native"
    client.max_tokens = 64
    client.dialogue_temperature = 0.7
    client.dialogue_top_p = 0.8
    client.dialogue_top_k = 20
    client.dialogue_presence_penalty = 1.5
    client.dialogue_enable_thinking = False
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    return client


def test_q38_06_qwen25_baseline_request_keeps_legacy_sampling():
    calls = []
    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
    client.model = "Qwen/Qwen2.5-72B-Instruct-AWQ"
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: calls.append(kwargs) or iter([])
    )))
    list(client.stream_messages(messages=[{"role": "user", "content": "hi"}]))
    assert calls[0]["temperature"] == 0.35
    assert calls[0]["top_p"] == 0.8
    assert "extra_body" not in calls[0]


def test_q38_08_candidate_stream_sends_non_thinking_body():
    calls = []
    client = _fake_chat_client(calls)
    assert list(client.stream_messages(messages=[
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hello"},
    ])) == ["ok"]
    assert calls[0]["temperature"] == 0.7
    assert calls[0]["presence_penalty"] == 1.5
    assert calls[0]["extra_body"] == {
        "top_k": 20,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def test_q38_09_candidate_complete_sends_non_thinking_body():
    calls = []
    client = _fake_chat_client(calls)
    assert client.complete_messages(
        messages=[{"role": "user", "content": "hello"}], max_tokens=12
    ) == "reply"
    assert calls[0]["temperature"] == 0.7
    assert calls[0]["presence_penalty"] == 1.5
    assert calls[0]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_q38_12_stream_and_complete_share_candidate_generation_settings():
    calls = []
    client = _fake_chat_client(calls)
    list(client.stream_messages(messages=[{"role": "user", "content": "hello"}]))
    client.complete_messages(messages=[{"role": "user", "content": "hello"}], max_tokens=12)
    for call in calls:
        assert call["temperature"] == 0.7
        assert call["top_p"] == 0.8
        assert call["presence_penalty"] == 1.5
        assert call["extra_body"] == {
            "top_k": 20,
            "chat_template_kwargs": {"enable_thinking": False},
        }


def test_q38_13_candidate_preserves_complete_message_history():
    calls = []
    client = _fake_chat_client(calls)
    messages = [
        {"role": "system", "content": "frozen prompt"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "second"},
    ]
    list(client.stream_messages(messages=messages))
    assert calls[0]["messages"] == messages


def test_q38_14_candidate_consumes_delta_content_not_reasoning_fields():
    calls = []
    chunks = [SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(reasoning_content="hidden", content="visible")
    )])]
    client = _fake_chat_client(calls, chunks=chunks)
    assert list(client.stream_messages(messages=[{"role": "user", "content": "hello"}])) == ["visible"]


def test_q38_15_candidate_keeps_dialogue_and_agent_ports(tmp_path):
    config = load_runtime_config(_write_config(tmp_path))
    assert config.dialogue.base_url == "http://127.0.0.1:8000/v1"


def test_q38_16_candidate_factory_transfers_generation_contract(tmp_path):
    config = load_runtime_config(_write_config(tmp_path))
    client = build_dialogue_client(config)
    assert client.model == "Qwen/Qwen3.8-27B-FP8"
    assert client.dialogue_top_k == 20
    assert client.dialogue_temperature == 0.7
    assert client.dialogue_presence_penalty == 1.5
    assert client.dialogue_enable_thinking is False


def test_q38_17_candidate_keeps_streaming_tts_enabled_without_changing_provider(tmp_path):
    config = load_runtime_config(_write_config(tmp_path))
    assert config.runtime.enable_streaming_tts is True
    assert config.tts.provider == "voxcpm"


def test_q38_18_prompt_contract_remains_external_to_the_configuration():
    from config import SYSTEM_PROMPT

    assert "本轮动作已经由系统确定" in SYSTEM_PROMPT
