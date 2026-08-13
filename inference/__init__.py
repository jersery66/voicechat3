"""Provider-neutral inference client contracts."""

from inference.dialogue_client import DialogueClient
from inference.factory import build_dialogue_client, build_guard_client, build_safety_gate
from inference.guard_client import GuardClient
from inference.router_client import RouterClient
from inference.vllm_client import VLLMOpenAIClient
from inference.vllm_guard_client import VLLMGuardClient

__all__ = [
    "DialogueClient", "GuardClient", "RouterClient", "VLLMOpenAIClient", "VLLMGuardClient",
    "build_dialogue_client", "build_guard_client", "build_safety_gate",
]
