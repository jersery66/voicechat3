"""Config-driven runtime configuration for development and deployment machines.

Runtime selection is config-driven: ``deployment/runtime_config.py`` loads
one YAML file (default ``config/runtime.yaml``) that names every model,
engine, endpoint, and generation parameter. The application never inspects
the GPU to choose a model.

Exports are resolved **lazily** on purpose. ``runtime_config`` imports PyYAML,
and this package also hosts the provisioning tools (``dependencies``,
``autostart``) that must run on a machine where nothing is installed yet — the
fresh-device path is exactly the one where ``yaml`` is missing. An eager
``from deployment.runtime_config import ...`` here breaks that path with
``ModuleNotFoundError: No module named 'yaml'`` before pip has had a chance to
run, which is how the first-run bootstrap used to fail.

The former ``deployment.profiles`` module (per-GPU deployment profiles) is
retired; its content is preserved under ``archive/profiles.py``.
"""

from __future__ import annotations

import importlib
from typing import Any

# Public names and the module that defines them.
_EXPORTS: dict[str, str] = {
    "AgentConfig": "deployment.runtime_config",
    "ASRConfig": "deployment.runtime_config",
    "DialogueConfig": "deployment.runtime_config",
    "RuntimeConfig": "deployment.runtime_config",
    "RuntimeConfigError": "deployment.runtime_config",
    "RuntimeSection": "deployment.runtime_config",
    "TTSConfig": "deployment.runtime_config",
    "get_runtime_config": "deployment.runtime_config",
    "load_runtime_config": "deployment.runtime_config",
    "reset_runtime_config_cache": "deployment.runtime_config",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Import a public name on first access (PEP 562)."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(list(globals()) + __all__))
