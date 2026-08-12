"""Agent readiness uses the same configured timeout as production routing."""

from services.agent_service import AgentService
from config import AGENT_TIMEOUT


def test_agent_client_uses_configured_timeout():
    agent = AgentService()
    assert agent.client.timeout == AGENT_TIMEOUT
