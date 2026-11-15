# TTS Service - provider selector
#
# The production speech backend is selected by runtime configuration
# (config/runtime.yaml -> tts.provider):
#
#   voxcpm     -> services.tts_service_voxcpm     (VoxCPM2, production default)
#   cosyvoice  -> services.tts_service_cosyvoice  (experimental backend)
#
# Provider modules are imported lazily: importing this selector never
# pulls the heavy model dependencies of an unselected backend. An
# unknown provider is a hard error - a typo in the configuration must
# not silently fall back to the wrong voice. Which provider values exist
# is enforced by deployment/runtime_config.py at config load time; the
# mapping here is the single place that wires a provider name to its
# implementation module.

from __future__ import annotations

from typing import Optional

# Provider name -> implementation module (imported lazily by _provider_module).
_TTS_PROVIDER_MODULES = {
    "voxcpm": "services.tts_service_voxcpm",
    "cosyvoice": "services.tts_service_cosyvoice",
}

TTS_PROVIDERS = tuple(sorted(_TTS_PROVIDER_MODULES))


def _provider_module(provider: str):
    """Return the implementation module for ``provider``.

    Raises ``ValueError`` for unknown providers so a misconfiguration
    fails loudly at selection time instead of silently choosing a
    default voice.
    """
    module_name = _TTS_PROVIDER_MODULES.get(provider)
    if module_name is None:
        raise ValueError(
            f"unknown TTS provider {provider!r}; "
            f"supported providers: {', '.join(TTS_PROVIDERS)}"
        )
    import importlib

    return importlib.import_module(module_name)


def _selected_provider(provider: Optional[str] = None) -> str:
    if provider is not None:
        return provider
    from config import TTS_PROVIDER

    return TTS_PROVIDER


def build_tts_service(provider: Optional[str] = None):
    """Build the TTS service for the configured (or given) provider."""
    return _provider_module(_selected_provider(provider)).TTSService()


def get_tts_service(provider: Optional[str] = None):
    """Return the provider's shared singleton instance.

    The singleton lives in the selected provider module, so every caller
    (UI, adapter factory) shares one instance per backend.
    """
    return _provider_module(_selected_provider(provider)).get_tts_service()
