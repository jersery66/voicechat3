"""Provider-neutral inference client contracts."""

from inference.dialogue_client import DialogueClient
from inference.openai_client import OpenAICompatibleClient
from inference.router_client import RouterClient

__all__ = ["DialogueClient", "RouterClient", "OpenAICompatibleClient"]
