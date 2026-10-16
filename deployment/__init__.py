"""Explicit runtime configuration for development and deployment machines.

Runtime selection is config-driven: ``deployment/runtime_config.py`` loads
one YAML file (default ``config/runtime.yaml``) that names every model,
engine, endpoint, and generation parameter. The application never inspects
the GPU to choose a model.
"""

from deployment.profiles import (
    DeploymentProfile,
    RuntimeModels,
    get_deployment_profile,
    resolve_runtime_models,
)
from deployment.runtime_config import (
    AgentConfig,
    ASRConfig,
    DialogueConfig,
    RuntimeConfig,
    RuntimeConfigError,
    RuntimeSection,
    TTSConfig,
    get_runtime_config,
    load_runtime_config,
    reset_runtime_config_cache,
)

__all__ = [
    "DeploymentProfile",
    "RuntimeModels",
    "get_deployment_profile",
    "resolve_runtime_models",
    "AgentConfig",
    "ASRConfig",
    "DialogueConfig",
    "RuntimeConfig",
    "RuntimeConfigError",
    "RuntimeSection",
    "TTSConfig",
    "get_runtime_config",
    "load_runtime_config",
    "reset_runtime_config_cache",
]
