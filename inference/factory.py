"""Build the inference client from the runtime configuration."""

from __future__ import annotations

from typing import Optional

from deployment.runtime_config import (
    PROVIDER_OPENAI_COMPATIBLE,
    RuntimeConfig,
    get_runtime_config,
)
from inference.openai_client import OpenAICompatibleClient


def build_dialogue_client(config: Optional[RuntimeConfig] = None, *,
                          timeout: Optional[float] = None) -> Optional[OpenAICompatibleClient]:
    """Build the dialogue client for the configured OpenAI-compatible provider.

    Any engine that serves an OpenAI-compatible ``/v1`` endpoint works
    (llama.cpp, vLLM, ...); the engine is a runtime-config choice, not a
    code path. The Ollama provider stays behind the legacy ``LLMService``
    during the compatibility migration, so this factory returns ``None``
    for it rather than constructing a duplicate client with different
    history semantics.
    """
    config = config if config is not None else get_runtime_config()
    dialogue = config.dialogue
    if dialogue.provider != PROVIDER_OPENAI_COMPATIBLE:
        return None
    return OpenAICompatibleClient(
        model=dialogue.model,
        base_url=dialogue.base_url,
        api_key=dialogue.api_key,
        timeout=dialogue.timeout if timeout is None else timeout,
        request_mode=dialogue.request_mode,
        system_role_mode=dialogue.system_role_mode,
        max_tokens=dialogue.max_tokens,
        dialogue_temperature=dialogue.temperature,
        dialogue_top_p=dialogue.top_p,
        dialogue_top_k=dialogue.top_k,
        dialogue_presence_penalty=dialogue.presence_penalty,
        dialogue_enable_thinking=dialogue.enable_thinking,
    )
