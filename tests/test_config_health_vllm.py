"""vLLM auxiliary models are diagnosed without blocking safe deterministic fallback."""

from scripts import check_config


def test_unavailable_vllm_agent_is_non_critical(monkeypatch):
    monkeypatch.setattr(check_config, "_check_openai_compatible_model", lambda *_: False)
    monkeypatch.setattr("config.AGENT_BACKEND", "vllm")
    monkeypatch.setattr("config.AGENT_MODEL", "agent-model")
    monkeypatch.setattr("config.AGENT_MODEL_SERVER", "http://agent:8001/v1")

    assert check_config.check_agent_backend() is True


def test_unavailable_vllm_guard_is_non_critical(monkeypatch):
    monkeypatch.setattr(check_config, "_check_openai_compatible_model", lambda *_: False)
    monkeypatch.setattr("config.GUARD_BACKEND", "vllm")
    monkeypatch.setattr("config.GUARD_MODEL", "guard-model")
    monkeypatch.setattr("config.GUARD_MODEL_SERVER", "http://guard:8002/v1")

    assert check_config.check_guard_backend() is True
