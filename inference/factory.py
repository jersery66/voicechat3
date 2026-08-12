"""Choose an inference adapter from the explicit deployment profile."""

from __future__ import annotations

import os
from typing import Optional

from deployment.profiles import DeploymentProfile, RuntimeModels
from inference.vllm_client import VLLMOpenAIClient


def build_dialogue_client(profile: DeploymentProfile, models: RuntimeModels,
                          *, timeout: float = 120.0) -> Optional[VLLMOpenAIClient]:
    """Build the external dialogue client when the profile selects vLLM.

    Ollama remains behind the legacy ``LLMService`` during the compatibility
    migration, so this factory returns ``None`` for an Ollama profile rather
    than constructing a duplicate client with different history semantics.
    """
    if profile.runtime_backend != "vllm":
        return None
    return VLLMOpenAIClient(
        model=models.dialogue,
        base_url=os.environ.get("VOICECHAT_DIALOGUE_BASE_URL", profile.dialogue_base_url),
        timeout=timeout,
    )
