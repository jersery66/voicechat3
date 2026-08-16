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
    base_url = (
        profile.dialogue_base_url
        if profile.name in {"a100_80g", "a100_80g_qwen38_candidate"}
        else os.environ.get("VOICECHAT_DIALOGUE_BASE_URL", profile.dialogue_base_url)
    )
    return VLLMOpenAIClient(
        model=models.dialogue,
        base_url=base_url,
        timeout=timeout,
        request_mode=profile.vllm_request_mode,
        system_role_mode=profile.vllm_system_role_mode,
        max_tokens=profile.dialogue_max_tokens,
        dialogue_temperature=profile.dialogue_temperature,
        dialogue_top_p=profile.dialogue_top_p,
        dialogue_top_k=profile.dialogue_top_k,
        dialogue_presence_penalty=profile.dialogue_presence_penalty,
        dialogue_enable_thinking=profile.dialogue_enable_thinking,
    )
