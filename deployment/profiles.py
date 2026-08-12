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
    router_model: str
    optional_guard_model: Optional[str]
    enable_streaming_tts: bool
    notes: str = ""


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
        router_model="qwen2.5:3b",
        optional_guard_model=None,
        enable_streaming_tts=False,
        notes="Local UI and smoke-test profile; it is not a production capacity target.",
    ),
    "a100_80g": DeploymentProfile(
        name="a100_80g",
        expected_gpu_memory_gb=80,
        runtime_backend="ollama",
        dialogue_model="qwen2.5:72b",
        router_model="qwen2.5:3b",
        optional_guard_model="qwen3guard:4b",
        enable_streaming_tts=True,
        notes="Single A100 80GB profile. Qwen2.5 72B remains the verified dialogue baseline.",
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
        dialogue=environment.get("OLLAMA_MODEL") or profile.dialogue_model,
        router=environment.get("AGENT_MODEL") or profile.router_model,
        optional_guard=environment.get("VOICECHAT_GUARD_MODEL") or profile.optional_guard_model,
    )
