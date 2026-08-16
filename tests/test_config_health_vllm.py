"""vLLM auxiliary models are diagnosed without blocking safe deterministic fallback."""

from scripts import check_config


def test_run_check_includes_runtime_dependencies_but_not_legacy_guard(monkeypatch):
    calls = []
    checks = (
        "check_offline_model_root",
        "check_dialogue_backend",
        "check_agent_backend",
        "check_funasr",
        "check_cosyvoice",
        "check_voxcpm",
        "check_voice_prompt",
        "check_knowledge_base",
        "check_converted_knowledge_base",
        "check_relaxation_media",
        "check_data_root",
    )
    for name in checks:
        monkeypatch.setattr(check_config, name, lambda name=name: calls.append(name) or True)

    assert not hasattr(check_config, "check_guard_backend")
    assert check_config.run_check() is True
    assert calls == list(checks)


def test_unavailable_vllm_agent_is_non_critical(monkeypatch):
    monkeypatch.setattr(check_config, "_check_openai_compatible_model", lambda *_: False)
    monkeypatch.setattr("config.AGENT_BACKEND", "vllm")
    monkeypatch.setattr("config.AGENT_MODEL", "agent-model")
    monkeypatch.setattr("config.AGENT_MODEL_SERVER", "http://agent:8001/v1")

    assert check_config.check_agent_backend() is True


def test_unavailable_a100_vllm_agent_blocks_production_readiness(monkeypatch):
    monkeypatch.setattr(check_config, "_check_openai_compatible_model", lambda *_: False)
    monkeypatch.setattr("config.AGENT_BACKEND", "vllm")
    monkeypatch.setattr("config.AGENT_MODEL", "agent-model")
    monkeypatch.setattr("config.AGENT_MODEL_SERVER", "http://agent:8001/v1")
    monkeypatch.setattr(
        check_config,
        "get_deployment_profile",
        lambda: type("Profile", (), {
            "name": "a100_80g",
            "strict_preflight": True,
        })(),
    )

    assert check_config.check_agent_backend() is False
