"""Agent readiness uses the same configured timeout as production routing."""

from services.agent_service import AgentService
from config import AGENT_TIMEOUT
import config


def test_agent_client_uses_configured_timeout():
    agent = AgentService()
    assert agent.client.timeout == AGENT_TIMEOUT


def test_default_agent_model_matches_local_deployment_model():
    assert config.AGENT_MODEL == "qwen2.5:3b"
