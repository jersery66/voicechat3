"""Auxiliary model services are diagnosed without blocking safe fallbacks."""

from types import SimpleNamespace

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


def test_unavailable_agent_is_non_critical_for_lenient_runtimes(monkeypatch):
    monkeypatch.setattr(check_config, "_check_openai_compatible_model", lambda *_: False)
    monkeypatch.setattr(check_config, "get_runtime_config", lambda: SimpleNamespace(
        runtime=SimpleNamespace(strict_preflight=False),
    ))
    monkeypatch.setattr("config.AGENT_PROVIDER", "openai_compatible")
    monkeypatch.setattr("config.AGENT_MODEL", "agent-model")
    monkeypatch.setattr("config.AGENT_MODEL_SERVER", "http://agent:8001/v1")

    assert check_config.check_agent_backend() is True


def test_unavailable_agent_blocks_strict_runtime_readiness(monkeypatch):
    monkeypatch.setattr(check_config, "_check_openai_compatible_model", lambda *_: False)
    monkeypatch.setattr(check_config, "get_runtime_config", lambda: SimpleNamespace(
        runtime=SimpleNamespace(strict_preflight=True),
    ))
    monkeypatch.setattr("config.AGENT_PROVIDER", "openai_compatible")
    monkeypatch.setattr("config.AGENT_MODEL", "agent-model")
    monkeypatch.setattr("config.AGENT_MODEL_SERVER", "http://agent:8001/v1")

    assert check_config.check_agent_backend() is False


def test_deterministic_escalation_skips_agent_probe_under_strict_runtime(monkeypatch):
    # The config contract says the agent server (:8001) is not started when
    # the decision chain never falls back to it; the strict preflight must
    # therefore not probe (and not fail on) the agent endpoint.
    monkeypatch.setattr("config.AGENT_ENABLED", True)
    monkeypatch.setattr(
        check_config, "_check_openai_compatible_model",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not probe")),
    )
    monkeypatch.setattr(check_config, "get_runtime_config", lambda: SimpleNamespace(
        runtime=SimpleNamespace(strict_preflight=True),
        decision=SimpleNamespace(mode="jev", escalation_mode="deterministic"),
    ))

    assert check_config.check_agent_backend() is True


def test_legacy_escalation_still_probes_agent_endpoint(monkeypatch):
    monkeypatch.setattr("config.AGENT_ENABLED", True)
    monkeypatch.setattr(check_config, "_check_openai_compatible_model", lambda *_: False)
    monkeypatch.setattr(check_config, "get_runtime_config", lambda: SimpleNamespace(
        runtime=SimpleNamespace(strict_preflight=True),
        decision=SimpleNamespace(mode="jev", escalation_mode="legacy"),
    ))
    monkeypatch.setattr("config.AGENT_PROVIDER", "openai_compatible")
    monkeypatch.setattr("config.AGENT_MODEL", "agent-model")
    monkeypatch.setattr("config.AGENT_MODEL_SERVER", "http://agent:8001/v1")

    assert check_config.check_agent_backend() is False


def test_disabled_agent_is_reported_and_non_critical(monkeypatch):
    monkeypatch.setattr("config.AGENT_ENABLED", False)
    monkeypatch.setattr(check_config, "_check_openai_compatible_model",
                        lambda *_: (_ for _ in ()).throw(AssertionError("must not probe")))

    assert check_config.check_agent_backend() is True


def test_unselected_tts_provider_is_reported_not_probed(monkeypatch, capsys):
    monkeypatch.setattr("config.TTS_PROVIDER", "cosyvoice")

    assert check_config.check_voxcpm() is True
    assert "not the configured TTS provider" in capsys.readouterr().out


def test_unselected_asr_provider_is_reported_not_probed(monkeypatch, capsys):
    monkeypatch.setattr("config.ASR_PROVIDER", "not-funasr")

    assert check_config.check_funasr() is True
    assert "not the configured ASR provider" in capsys.readouterr().out
