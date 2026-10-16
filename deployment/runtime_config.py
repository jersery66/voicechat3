"""Config-driven runtime: one YAML selects models, engines, and endpoints.

Design contract:

- The application never inspects the GPU to choose a model. Every model,
  engine, endpoint, and generation parameter comes from one runtime config
  file (default ``config/runtime.yaml``; override the path with the
  ``VOICECHAT_RUNTIME_CONFIG`` environment variable).
- Switching GPU, dialogue model, Agent model, or inference engine
  (llama.cpp / vLLM / Ollama) is a config edit, never a code change.
- Dialogue and Agent are configured independently: they may use different
  providers, endpoints, and models.
- Unknown keys, unknown providers, and out-of-range values are hard
  errors. A typo must fail loudly at startup instead of silently
  selecting a different model or engine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_CONFIG_PATH = APP_ROOT / "config" / "runtime.yaml"
RUNTIME_CONFIG_ENV_VAR = "VOICECHAT_RUNTIME_CONFIG"

# Dialogue / Agent providers. ``openai_compatible`` covers llama.cpp
# (llama-server), vLLM (vllm serve) and any other OpenAI-compatible
# endpoint; ``ollama`` is the native Ollama SDK kept for local development.
PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"
PROVIDER_OLLAMA = "ollama"
DIALOGUE_PROVIDERS = frozenset({PROVIDER_OPENAI_COMPATIBLE, PROVIDER_OLLAMA})

# Speech engine providers.
ASR_PROVIDER_FUNASR = "funasr"
ASR_PROVIDERS = frozenset({ASR_PROVIDER_FUNASR})
TTS_PROVIDER_VOXCPM = "voxcpm"
TTS_PROVIDER_COSYVOICE = "cosyvoice"
TTS_PROVIDERS = frozenset({TTS_PROVIDER_VOXCPM, TTS_PROVIDER_COSYVOICE})

# Shared request contract values.
REQUEST_MODES = frozenset({"chat", "completion"})
SYSTEM_ROLE_MODES = frozenset({"native", "prepend_user"})

# ``model_path: auto`` (or null) means: use the config.py auto-detection.
PATH_AUTO = "auto"

_TOP_LEVEL_SECTIONS = ("dialogue", "agent", "asr", "tts", "runtime")
_DIALOGUE_KEYS = (
    "provider", "base_url", "model", "api_key", "timeout", "max_tokens",
    "temperature", "top_p", "top_k", "presence_penalty", "enable_thinking",
    "request_mode", "system_role_mode", "system_prompt_override",
)
_AGENT_KEYS = ("enabled", "provider", "base_url", "model", "api_key", "timeout", "temperature")
_ASR_KEYS = ("provider", "model_path")
_TTS_KEYS = ("provider", "model_path", "voice_prompt_path")
_RUNTIME_KEYS = (
    "strict_preflight", "immutable_contract", "manage_model_servers",
    "enable_streaming_tts",
)

# Legacy dev-shell overrides, honored only when the runtime contract is not
# immutable. Production configs set ``immutable_contract: true`` so the YAML
# file is the single source of truth and experiments stay reproducible.
_ENV_DIALOGUE_BASE_URL = "VOICECHAT_DIALOGUE_BASE_URL"
_ENV_DIALOGUE_MODEL = "VOICECHAT_DIALOGUE_MODEL"
_ENV_DIALOGUE_MODEL_LEGACY = "VOICECHAT_VLLM_MODEL"
_ENV_DIALOGUE_MODEL_OLLAMA = "OLLAMA_MODEL"
_ENV_AGENT_BASE_URL = "VOICECHAT_AGENT_BASE_URL"
_ENV_AGENT_MODEL = "AGENT_MODEL"


class RuntimeConfigError(ValueError):
    """Raised when a runtime config file is missing, malformed, or invalid."""


@dataclass(frozen=True)
class DialogueConfig:
    """Main dialogue model service. Any OpenAI-compatible endpoint works."""

    provider: str
    base_url: str
    model: str
    api_key: str = "EMPTY"
    timeout: float = 120.0
    max_tokens: int = 1024
    temperature: float = 0.35
    top_p: float = 0.8
    top_k: Optional[int] = None
    presence_penalty: Optional[float] = None
    enable_thinking: Optional[bool] = None
    request_mode: str = "chat"
    system_role_mode: str = "native"
    system_prompt_override: Optional[str] = None


@dataclass(frozen=True)
class AgentConfig:
    """Agent model service, configured independently of the dialogue model."""

    enabled: bool = True
    provider: str = PROVIDER_OPENAI_COMPATIBLE
    base_url: str = ""
    model: str = ""
    api_key: str = "EMPTY"
    timeout: float = 10.0
    temperature: float = 0.1


@dataclass(frozen=True)
class ASRConfig:
    """Speech recognition engine selection."""

    provider: str = ASR_PROVIDER_FUNASR
    model_path: Optional[str] = None


@dataclass(frozen=True)
class TTSConfig:
    """Speech synthesis engine selection."""

    provider: str = TTS_PROVIDER_VOXCPM
    model_path: Optional[str] = None
    voice_prompt_path: Optional[str] = None


@dataclass(frozen=True)
class RuntimeSection:
    """Process-level runtime policy flags."""

    strict_preflight: bool = False
    immutable_contract: bool = True
    manage_model_servers: bool = False
    enable_streaming_tts: bool = True


@dataclass(frozen=True)
class RuntimeConfig:
    """One fully resolved runtime configuration."""

    dialogue: DialogueConfig
    agent: AgentConfig
    asr: ASRConfig
    tts: TTSConfig
    runtime: RuntimeSection
    source_path: str = ""

    @property
    def dialogue_is_openai_compatible(self) -> bool:
        return self.dialogue.provider == PROVIDER_OPENAI_COMPATIBLE

    @property
    def dialogue_is_ollama(self) -> bool:
        return self.dialogue.provider == PROVIDER_OLLAMA

    @property
    def agent_is_openai_compatible(self) -> bool:
        return self.agent.provider == PROVIDER_OPENAI_COMPATIBLE

    @property
    def agent_is_ollama(self) -> bool:
        return self.agent.provider == PROVIDER_OLLAMA

    def summary(self) -> dict[str, Any]:
        """Return a log/probe-friendly view of the effective configuration."""
        return {
            "source": self.source_path,
            "dialogue": {
                "provider": self.dialogue.provider,
                "base_url": self.dialogue.base_url,
                "model": self.dialogue.model,
                "max_tokens": self.dialogue.max_tokens,
                "temperature": self.dialogue.temperature,
                "enable_thinking": self.dialogue.enable_thinking,
            },
            "agent": {
                "enabled": self.agent.enabled,
                "provider": self.agent.provider,
                "base_url": self.agent.base_url,
                "model": self.agent.model,
            },
            "asr": {"provider": self.asr.provider, "model_path": self.asr.model_path},
            "tts": {"provider": self.tts.provider, "model_path": self.tts.model_path},
            "runtime": {
                "strict_preflight": self.runtime.strict_preflight,
                "immutable_contract": self.runtime.immutable_contract,
                "manage_model_servers": self.runtime.manage_model_servers,
                "enable_streaming_tts": self.runtime.enable_streaming_tts,
            },
        }


# ---------------------------------------------------------------------------
# Strict YAML field parsing
# ---------------------------------------------------------------------------

def _fail(message: str) -> RuntimeConfigError:
    return RuntimeConfigError(message)


def _require_section(raw: Mapping[str, Any], name: str, source: Path) -> Mapping[str, Any]:
    if name not in raw:
        raise _fail(
            f"runtime config {source} is missing the required '{name}' section; "
            f"expected sections: {', '.join(_TOP_LEVEL_SECTIONS)}"
        )
    section = raw[name]
    if section is None:
        raise _fail(f"runtime config section '{name}' is empty in {source}")
    if not isinstance(section, Mapping):
        raise _fail(f"runtime config section '{name}' must be a mapping in {source}")
    return section


def _reject_unknown_keys(section: Mapping[str, Any], allowed: tuple[str, ...],
                         name: str, source: Path) -> None:
    unknown = sorted(set(section) - set(allowed))
    if unknown:
        raise _fail(
            f"unknown key(s) {unknown} in runtime config section '{name}' ({source}); "
            f"allowed keys: {', '.join(allowed)}"
        )


def _required_str(section: Mapping[str, Any], key: str, name: str) -> str:
    if key not in section or section[key] is None:
        raise _fail(f"runtime config '{name}.{key}' is required")
    value = section[key]
    if not isinstance(value, str):
        raise _fail(f"runtime config '{name}.{key}' must be a string, got {type(value).__name__}")
    value = value.strip()
    if not value:
        raise _fail(f"runtime config '{name}.{key}' must not be empty")
    return value


def _optional_str(section: Mapping[str, Any], key: str, name: str,
                  default: Optional[str] = None) -> Optional[str]:
    if key not in section or section[key] is None:
        return default
    value = section[key]
    if not isinstance(value, str):
        raise _fail(f"runtime config '{name}.{key}' must be a string, got {type(value).__name__}")
    value = value.strip()
    return value or default


def _optional_bool(section: Mapping[str, Any], key: str, name: str,
                   default: Optional[bool] = None) -> Optional[bool]:
    if key not in section or section[key] is None:
        return default
    value = section[key]
    if not isinstance(value, bool):
        raise _fail(f"runtime config '{name}.{key}' must be true or false, got {value!r}")
    return value


def _required_bool(section: Mapping[str, Any], key: str, name: str, default: bool) -> bool:
    value = _optional_bool(section, key, name, default)
    assert value is not None
    return value


def _optional_number(section: Mapping[str, Any], key: str, name: str,
                     default: Optional[float] = None) -> Optional[float]:
    if key not in section or section[key] is None:
        return default
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(f"runtime config '{name}.{key}' must be a number, got {value!r}")
    return float(value)


def _optional_positive_int(section: Mapping[str, Any], key: str, name: str,
                           default: Optional[int] = None) -> Optional[int]:
    value = _optional_number(section, key, name, default)
    if value is None:
        return None
    if value != int(value) or int(value) < 1:
        raise _fail(f"runtime config '{name}.{key}' must be a positive integer, got {value!r}")
    return int(value)


def _require_http_url(value: str, name: str, key: str) -> str:
    if not value.startswith(("http://", "https://")):
        raise _fail(
            f"runtime config '{name}.{key}' must be an http(s) URL, got {value!r}"
        )
    return value


def _require_choice(value: str, allowed: frozenset[str], name: str, key: str) -> str:
    if value not in allowed:
        raise _fail(
            f"runtime config '{name}.{key}' must be one of {sorted(allowed)}, got {value!r}"
        )
    return value


def _parse_model_path(value: Optional[str], name: str) -> Optional[str]:
    """Normalize ``model_path``: ``auto``/empty become None (auto-detection)."""
    if value is None or value.lower() == PATH_AUTO:
        return None
    return value


def _parse_dialogue(section: Mapping[str, Any], source: Path) -> DialogueConfig:
    name = "dialogue"
    _reject_unknown_keys(section, _DIALOGUE_KEYS, name, source)
    provider = _require_choice(
        _required_str(section, "provider", name), DIALOGUE_PROVIDERS, name, "provider"
    )
    base_url = _require_http_url(
        _required_str(section, "base_url", name), name, "base_url"
    )
    model = _required_str(section, "model", name)
    request_mode = _require_choice(
        _optional_str(section, "request_mode", name, "chat") or "chat",
        REQUEST_MODES, name, "request_mode",
    )
    system_role_mode = _require_choice(
        _optional_str(section, "system_role_mode", name, "native") or "native",
        SYSTEM_ROLE_MODES, name, "system_role_mode",
    )
    timeout = _optional_number(section, "timeout", name, 120.0)
    if timeout is None or timeout <= 0:
        raise _fail(f"runtime config '{name}.timeout' must be positive, got {timeout!r}")
    temperature = _optional_number(section, "temperature", name, 0.35)
    if temperature is None or temperature < 0:
        raise _fail(f"runtime config '{name}.temperature' must be non-negative, got {temperature!r}")
    top_p = _optional_number(section, "top_p", name, 0.8)
    if top_p is None or not 0 < top_p <= 1:
        raise _fail(f"runtime config '{name}.top_p' must be in (0, 1], got {top_p!r}")
    top_k = _optional_positive_int(section, "top_k", name)
    presence_penalty = _optional_number(section, "presence_penalty", name)
    return DialogueConfig(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=_optional_str(section, "api_key", name, "EMPTY") or "EMPTY",
        timeout=timeout,
        max_tokens=_optional_positive_int(section, "max_tokens", name, 1024),
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        presence_penalty=presence_penalty,
        enable_thinking=_optional_bool(section, "enable_thinking", name),
        request_mode=request_mode,
        system_role_mode=system_role_mode,
        system_prompt_override=_optional_str(section, "system_prompt_override", name),
    )


def _parse_agent(section: Mapping[str, Any], source: Path) -> AgentConfig:
    name = "agent"
    _reject_unknown_keys(section, _AGENT_KEYS, name, source)
    enabled = _required_bool(section, "enabled", name, True)
    provider = _require_choice(
        _optional_str(section, "provider", name, PROVIDER_OPENAI_COMPATIBLE)
        or PROVIDER_OPENAI_COMPATIBLE,
        DIALOGUE_PROVIDERS, name, "provider",
    )
    base_url = _optional_str(section, "base_url", name, "") or ""
    model = _optional_str(section, "model", name, "") or ""
    if enabled:
        if not base_url:
            raise _fail(
                f"runtime config 'agent.enabled' is true but 'agent.base_url' is missing"
            )
        if not model:
            raise _fail(
                f"runtime config 'agent.enabled' is true but 'agent.model' is missing"
            )
        base_url = _require_http_url(base_url, name, "base_url")
    timeout = _optional_number(section, "timeout", name, 10.0)
    if timeout is None or timeout <= 0:
        raise _fail(f"runtime config '{name}.timeout' must be positive, got {timeout!r}")
    temperature = _optional_number(section, "temperature", name, 0.1)
    if temperature is None or temperature < 0:
        raise _fail(f"runtime config '{name}.temperature' must be non-negative, got {temperature!r}")
    return AgentConfig(
        enabled=enabled,
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=_optional_str(section, "api_key", name, "EMPTY") or "EMPTY",
        timeout=timeout,
        temperature=temperature,
    )


def _parse_asr(section: Mapping[str, Any], source: Path) -> ASRConfig:
    name = "asr"
    _reject_unknown_keys(section, _ASR_KEYS, name, source)
    provider = _require_choice(
        _optional_str(section, "provider", name, ASR_PROVIDER_FUNASR) or ASR_PROVIDER_FUNASR,
        ASR_PROVIDERS, name, "provider",
    )
    return ASRConfig(
        provider=provider,
        model_path=_parse_model_path(_optional_str(section, "model_path", name), name),
    )


def _parse_tts(section: Mapping[str, Any], source: Path) -> TTSConfig:
    name = "tts"
    _reject_unknown_keys(section, _TTS_KEYS, name, source)
    provider = _require_choice(
        _optional_str(section, "provider", name, TTS_PROVIDER_VOXCPM) or TTS_PROVIDER_VOXCPM,
        TTS_PROVIDERS, name, "provider",
    )
    return TTSConfig(
        provider=provider,
        model_path=_parse_model_path(_optional_str(section, "model_path", name), name),
        voice_prompt_path=_parse_model_path(
            _optional_str(section, "voice_prompt_path", name), name
        ),
    )


def _parse_runtime(section: Mapping[str, Any], source: Path) -> RuntimeSection:
    name = "runtime"
    _reject_unknown_keys(section, _RUNTIME_KEYS, name, source)
    return RuntimeSection(
        strict_preflight=_required_bool(section, "strict_preflight", name, False),
        immutable_contract=_required_bool(section, "immutable_contract", name, True),
        manage_model_servers=_required_bool(section, "manage_model_servers", name, False),
        enable_streaming_tts=_required_bool(section, "enable_streaming_tts", name, True),
    )


def _apply_env_overrides(config: RuntimeConfig,
                         environment: Mapping[str, str]) -> RuntimeConfig:
    """Apply legacy dev-shell overrides; disabled for immutable contracts."""
    if config.runtime.immutable_contract:
        return config
    dialogue = config.dialogue
    base_url = environment.get(_ENV_DIALOGUE_BASE_URL)
    if base_url:
        dialogue = replace(dialogue, base_url=base_url.strip())
    model = (
        environment.get(_ENV_DIALOGUE_MODEL)
        or environment.get(_ENV_DIALOGUE_MODEL_LEGACY)
        or environment.get(_ENV_DIALOGUE_MODEL_OLLAMA)
    )
    if model:
        dialogue = replace(dialogue, model=model.strip())
    agent = config.agent
    agent_base_url = environment.get(_ENV_AGENT_BASE_URL)
    if agent_base_url and agent.enabled:
        agent = replace(agent, base_url=agent_base_url.strip())
    agent_model = environment.get(_ENV_AGENT_MODEL)
    if agent_model and agent.enabled:
        agent = replace(agent, model=agent_model.strip())
    return replace(config, dialogue=dialogue, agent=agent)


def runtime_config_path() -> Path:
    """Resolve the runtime config path from the environment or the default."""
    override = os.environ.get(RUNTIME_CONFIG_ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_RUNTIME_CONFIG_PATH


def load_runtime_config(path: Optional[str | Path] = None,
                        *,
                        environment: Optional[Mapping[str, str]] = None) -> RuntimeConfig:
    """Load and strictly validate one runtime config file."""
    resolved = Path(path).expanduser() if path else runtime_config_path()
    if not resolved.is_file():
        raise _fail(
            f"runtime config file not found: {resolved} "
            f"(override the path with {RUNTIME_CONFIG_ENV_VAR})"
        )
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise _fail(f"runtime config {resolved} is not valid YAML: {exc}") from exc
    except OSError as exc:
        raise _fail(f"runtime config {resolved} could not be read: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise _fail(f"runtime config {resolved} must contain a YAML mapping at the top level")

    unknown_sections = sorted(set(raw) - set(_TOP_LEVEL_SECTIONS))
    if unknown_sections:
        raise _fail(
            f"unknown section(s) {unknown_sections} in runtime config {resolved}; "
            f"expected sections: {', '.join(_TOP_LEVEL_SECTIONS)}"
        )

    config = RuntimeConfig(
        dialogue=_parse_dialogue(_require_section(raw, "dialogue", resolved), resolved),
        agent=_parse_agent(_require_section(raw, "agent", resolved), resolved),
        asr=_parse_asr(_require_section(raw, "asr", resolved), resolved),
        tts=_parse_tts(_require_section(raw, "tts", resolved), resolved),
        runtime=_parse_runtime(_require_section(raw, "runtime", resolved), resolved),
        source_path=str(resolved),
    )
    env = os.environ if environment is None else environment
    return _apply_env_overrides(config, env)


_cached_config: Optional[RuntimeConfig] = None


def get_runtime_config() -> RuntimeConfig:
    """Return the process-wide runtime config, loading it once."""
    global _cached_config
    if _cached_config is None:
        _cached_config = load_runtime_config()
    return _cached_config


def reset_runtime_config_cache() -> None:
    """Drop the cached config (test helper; the next load re-reads the file)."""
    global _cached_config
    _cached_config = None
