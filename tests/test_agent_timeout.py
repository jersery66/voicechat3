"""Agent readiness uses the same configured timeout as production routing."""

from services.agent_service import AgentService
from config import AGENT_TIMEOUT
import config
from types import SimpleNamespace


def test_agent_client_uses_configured_timeout():
    agent = AgentService()
    assert agent.client.timeout == AGENT_TIMEOUT


def test_default_agent_model_matches_local_deployment_model():
    profile = config.DEPLOYMENT_PROFILE
    assert config.AGENT_MODEL == profile.agent_model


def test_agent_uses_profile_selected_vllm_endpoint():
    profile = config.DEPLOYMENT_PROFILE

    assert config.AGENT_BACKEND == profile.runtime_backend
    assert config.AGENT_MODEL_SERVER == profile.agent_base_url


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
