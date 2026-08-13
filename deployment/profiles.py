"""Runtime profiles are explicit; hardware detection never selects a model."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class DeploymentProfile:
    """Pinned runtime contract for one supported machine class.

    ``dialogue_model`` is the model that the profile can run today. Optional
    models are declared separately so a missing guard/router never silently
    changes the participant-facing dialogue model.
    """

    name: str
    expected_gpu_memory_gb: int
    runtime_backend: str
    dialogue_model: str
    dialogue_base_url: str
    router_model: str
    agent_model: str
    agent_base_url: str
    optional_guard_model: Optional[str]
    guard_base_url: Optional[str]
    enable_streaming_tts: bool
    notes: str = ""
    vllm_request_mode: str = "chat"
    vllm_system_role_mode: str = "native"
    dialogue_max_tokens: int = 1024
    system_prompt_override: Optional[str] = None


@dataclass(frozen=True)
class RuntimeModels:
    """Resolved model names after deliberate operator overrides."""

    dialogue: str
    router: str
    optional_guard: Optional[str]


_PROFILES = {
    "dev_6g": DeploymentProfile(
        name="dev_6g",
        expected_gpu_memory_gb=6,
        runtime_backend="ollama",
        dialogue_model="qwen2.5:3b",
        dialogue_base_url="http://localhost:11434",
        router_model="qwen2.5:3b",
        agent_model="qwen2.5:3b",
        agent_base_url="http://localhost:11434/v1",
        optional_guard_model=None,
        guard_base_url=None,
        enable_streaming_tts=False,
        notes="Local UI and smoke-test profile; it is not a production capacity target.",
    ),
    "dev_vllm_6g": DeploymentProfile(
        name="dev_vllm_6g",
        expected_gpu_memory_gb=6,
        runtime_backend="vllm",
        dialogue_model="gemma-2b-awq",
        dialogue_base_url="http://127.0.0.1:18000/v1",
        router_model="qwen2.5:3b",
        agent_model="qwen2.5:3b",
        agent_base_url="http://127.0.0.1:18001/v1",
        optional_guard_model=None,
        guard_base_url=None,
        enable_streaming_tts=False,
        vllm_request_mode="completion",
        dialogue_max_tokens=96,
        system_prompt_override="Reply briefly and directly to the user.",
        notes=(
            "Local vLLM integration profile for the Gemma 2B AWQ smoke server. "
            "It validates desktop-to-vLLM wiring and is not a production dialogue target."
        ),
    ),
    "a100_80g": DeploymentProfile(
        name="a100_80g",
        expected_gpu_memory_gb=80,
        runtime_backend="vllm",
        dialogue_model="Qwen/Qwen2.5-72B-Instruct-AWQ",
        dialogue_base_url="http://127.0.0.1:8000/v1",
        router_model="Qwen/Qwen2.5-3B-Instruct-AWQ",
        agent_model="Qwen/Qwen2.5-3B-Instruct-AWQ",
        agent_base_url="http://127.0.0.1:8001/v1",
        optional_guard_model=None,
        guard_base_url=None,
        enable_streaming_tts=True,
        notes=(
            "Single A100 80GB profile. Qwen2.5 72B AWQ dialogue and a 3B "
            "Agent run behind vLLM; the deterministic crisis policy is the "
            "sole production safety boundary."
        ),
    ),
}


def get_deployment_profile(name: Optional[str] = None) -> DeploymentProfile:
    """Return a supported profile, optionally selected by an environment variable."""
    selected = (name or os.environ.get("VOICECHAT_DEPLOYMENT_PROFILE", "dev_6g")).strip().lower()
    try:
        return _PROFILES[selected]
    except KeyError as exc:
        supported = ", ".join(sorted(_PROFILES))
        raise ValueError(
            f"Unknown deployment profile {selected!r}. Supported profiles: {supported}"
        ) from exc


def resolve_runtime_models(profile: DeploymentProfile,
                           environment: Mapping[str, str] | None = None) -> RuntimeModels:
    """Resolve only explicit overrides; live server inventory never selects a model."""
    environment = os.environ if environment is None else environment
    return RuntimeModels(
        dialogue=(
            environment.get("VOICECHAT_DIALOGUE_MODEL")
            or environment.get("VOICECHAT_VLLM_MODEL")
            or environment.get("OLLAMA_MODEL")
            or profile.dialogue_model
        ),
        router=environment.get("AGENT_MODEL") or profile.router_model,
        optional_guard=environment.get("VOICECHAT_GUARD_MODEL") or profile.optional_guard_model,
    )
