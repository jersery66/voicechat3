"""Agent readiness uses the same configured timeout as production routing."""

from dataclasses import replace

from services.agent_service import AgentService
from config import AGENT_TIMEOUT
import config
from deployment.runtime_config import get_runtime_config, load_runtime_config
from types import SimpleNamespace


def test_agent_client_uses_configured_timeout():
    agent = AgentService()
    assert agent.client.timeout == AGENT_TIMEOUT


def test_default_agent_model_matches_runtime_config():
    assert config.AGENT_MODEL == get_runtime_config().agent.model


def test_agent_uses_the_configured_endpoint():
    runtime = get_runtime_config()

    assert config.AGENT_PROVIDER == runtime.agent.provider
    assert config.AGENT_MODEL_SERVER == runtime.agent.base_url


def test_agent_and_dialogue_are_configured_independently():
    """主模型与 Agent 允许各自配置 provider、端点与模型。"""
    base = load_runtime_config()
    mixed = replace(
        base,
        dialogue=replace(
            base.dialogue,
            provider="ollama",
            base_url="http://127.0.0.1:11434",
            model="qwen2.5:3b",
        ),
    )

    assert mixed.dialogue.provider != mixed.agent.provider
    assert mixed.dialogue_is_ollama
    assert mixed.agent_is_openai_compatible
    assert mixed.dialogue.base_url != mixed.agent.base_url
    assert mixed.dialogue.model != mixed.agent.model


def test_agent_readiness_requires_the_profile_selected_model_on_its_server():
    agent = AgentService()
    agent.model = "configured-agent"

    class Models:
        def list(self):
            return SimpleNamespace(data=[SimpleNamespace(id="other-agent")])

    agent.client = SimpleNamespace(models=Models())

    assert agent.is_available() is False


def test_agent_readiness_accepts_openai_model_inventory_dicts():
    agent = AgentService()
    agent.model = "configured-agent"

    class Models:
        def list(self):
            return {"data": [{"id": "configured-agent"}]}

    agent.client = SimpleNamespace(models=Models())

    assert agent.is_available() is True


def test_disabled_agent_singleton_returns_none(monkeypatch):
    """agent.enabled: false 时不构造 Agent，调用方走主模型/关键词回退。"""
    monkeypatch.setattr("services.agent_service.AGENT_ENABLED", False)
    monkeypatch.setattr("services.agent_service._agent_service", None)

    from services.agent_service import get_agent_service

    assert get_agent_service() is None


def test_enabled_agent_singleton_is_shared(monkeypatch):
    monkeypatch.setattr("services.agent_service.AGENT_ENABLED", True)
    monkeypatch.setattr("services.agent_service._agent_service", None)

    from services.agent_service import get_agent_service

    agent = get_agent_service()
    assert isinstance(agent, AgentService)
    assert get_agent_service() is agent


def test_disabled_agent_is_unavailable_without_probing(monkeypatch):
    monkeypatch.setattr("services.agent_service.AGENT_ENABLED", False)
    agent = AgentService()

    class ExplodingModels:
        def list(self):
            raise AssertionError("disabled agent must not probe the network")

    agent.client = SimpleNamespace(models=ExplodingModels())

    assert agent.is_available() is False
