"""Per-session runtime provenance without secrets or absolute model paths.

Two schema versions exist in the wild:

- ``1`` — recorded a hardware ``deployment_profile`` name, the dialogue and
  agent models, and both endpoints. It predates the config-driven runtime, so
  it says nothing about providers, engines, or ASR/TTS selection.
- ``2`` — records the runtime configuration file plus every provider and
  engine, and no longer has a profile.

Sessions recorded before the config-driven merge cannot be regenerated, so
:func:`normalize_runtime_manifest` reads either version into one shape and
reports which provenance fields the older version simply did not record.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any
from typing import Mapping
from urllib.parse import urlparse, urlunsplit

from data.atomic_io import atomic_write_json
from deployment.runtime_config import (
    RuntimeConfig,
    get_runtime_config,
    load_runtime_config,
)

MANIFEST_ARTIFACT_TYPE = "SESSION_RUNTIME_MANIFEST"
MANIFEST_SCHEMA_VERSION = 2
SUPPORTED_MANIFEST_SCHEMA_VERSIONS = (1, 2)

# Fields a schema-1 file never carried. A reader must not read them as "off".
SCHEMA_2_ONLY_FIELDS = (
    "runtime_config",
    "dialogue_provider",
    "dialogue_engine",
    "agent_enabled",
    "agent_provider",
    "agent_engine",
    "asr_provider",
    "tts_provider",
)


class RuntimeManifestError(ValueError):
    """Raised when a manifest cannot be read or its schema is unsupported."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))


def _current_git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _git_dirty() -> bool | None:
    try:
        output = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return bool(output.strip())
    except (OSError, subprocess.SubprocessError):
        return None


def _basename_or_none(value: Any) -> str | None:
    if not value:
        return None
    return Path(str(value)).name


def _sanitized_url(value: str) -> str:
    """Keep the endpoint identity, drop anything that could carry a secret.

    A loopback ``base_url`` is provenance; ``user:password@`` or a token in the
    query string is not. Configured endpoints never carry either, so this only
    removes what should never reach a research artifact.
    """
    parsed = urlparse(value)
    if not (parsed.username or parsed.password or parsed.query or parsed.fragment):
        return value
    host = parsed.hostname or ""
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _resolve_config(
    runtime_config: RuntimeConfig | None,
    environment: Mapping[str, str] | None,
) -> RuntimeConfig:
    """Return the configuration that is actually in effect for this session.

    When ``environment`` is supplied the YAML is re-read with that mapping so
    the manifest reflects what the process would really use, not what the
    file alone says.  An immutable contract ignores those overrides by
    construction, which is the behaviour every session recorded through the
    default configuration relies on.
    """
    if environment is not None:
        source = runtime_config.source_path if runtime_config is not None else None
        return load_runtime_config(source or None, environment=environment)
    if runtime_config is not None:
        return runtime_config
    return get_runtime_config()


def build_runtime_manifest(
    runtime_config: RuntimeConfig | None = None,
    *,
    git_sha: str | None = None,
    started_at: str | None = None,
    app_version: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build a configuration/provenance manifest for one session.

    The manifest records the resolved runtime configuration — provider,
    model, and endpoint for dialogue and agent, plus the ASR/TTS providers —
    so a later reader can reproduce a session without opening the YAML file.
    Only the configuration file name is recorded, never its absolute path.
    """
    resolved = _resolve_config(runtime_config, environment)

    try:
        import config as app_config

        system_prompt = resolved.dialogue.system_prompt_override or getattr(app_config, "SYSTEM_PROMPT", "")
        stt_model = _basename_or_none(getattr(app_config, "FUNASR_MODEL_PATH", None))
        tts_model = _basename_or_none(getattr(app_config, "VOXCPM_MODEL_PATH", None))
        app_version = app_version or getattr(app_config, "APP_VERSION", None) or "UNVERSIONED"
    except Exception:
        system_prompt = resolved.dialogue.system_prompt_override or ""
        stt_model = None
        tts_model = None
        app_version = app_version or "UNVERSIONED"

    root = Path(__file__).resolve().parents[1]
    scale_hash = None
    try:
        from services.scales import SCALES

        scale_hash = _sha256_json(SCALES)
    except Exception:
        pass
    rag_hash = None
    rag_path = root / "knowledge_base" / "knowledge.json"
    if rag_path.exists():
        try:
            rag_hash = _sha256_bytes(rag_path.read_bytes())
        except OSError:
            pass
    catalog_hash = None
    try:
        from relaxation.catalog import build_default_catalog

        catalog_hash = _sha256_json([item.model_dump(mode="json") for item in build_default_catalog()])
    except Exception:
        pass

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "artifact_type": MANIFEST_ARTIFACT_TYPE,
        "generated_at": started_at or datetime.now(timezone.utc).isoformat(),
        "app_version": app_version,
        "git_sha": git_sha or _current_git_sha(),
        "runtime_config": _basename_or_none(resolved.source_path) or "UNSPECIFIED",
        "dialogue_provider": resolved.dialogue.provider,
        "dialogue_engine": resolved.dialogue.engine,
        "dialogue_model": resolved.dialogue.model,
        "dialogue_base_url": _sanitized_url(resolved.dialogue.base_url),
        "agent_enabled": resolved.agent.enabled,
        "agent_provider": resolved.agent.provider,
        "agent_engine": resolved.agent.engine,
        "agent_model": resolved.agent.model,
        "agent_base_url": _sanitized_url(resolved.agent.base_url) if resolved.agent.base_url else "",
        "asr_provider": resolved.asr.provider,
        "stt_model": stt_model,
        "tts_provider": resolved.tts.provider,
        "tts_model": tts_model,
        "prompt_version": _sha256_bytes(system_prompt.encode("utf-8")) if system_prompt else None,
        "effective_prompt_hash": _sha256_bytes(system_prompt.encode("utf-8")) if system_prompt else None,
        "scale_definition_hash": scale_hash,
        "rag_corpus_hash": rag_hash,
        "relaxation_catalog_version": catalog_hash,
        "git_dirty": _git_dirty(),
        "secrets_included": False,
    }


def write_runtime_manifest(path: str | Path, **kwargs: Any) -> Path:
    destination = Path(path)
    atomic_write_json(destination, build_runtime_manifest(**kwargs))
    return destination


_NORMALIZED_FIELDS = (
    "artifact_type",
    "generated_at",
    "app_version",
    "git_sha",
    "git_dirty",
    "runtime_config",
    "deployment_profile",
    "dialogue_provider",
    "dialogue_engine",
    "dialogue_model",
    "dialogue_base_url",
    "agent_enabled",
    "agent_provider",
    "agent_engine",
    "agent_model",
    "agent_base_url",
    "asr_provider",
    "stt_model",
    "tts_provider",
    "tts_model",
    "prompt_version",
    "effective_prompt_hash",
    "scale_definition_hash",
    "rag_corpus_hash",
    "relaxation_catalog_version",
    "secrets_included",
)


def normalize_runtime_manifest(payload: Any) -> dict[str, Any]:
    """Read a schema-1 or schema-2 manifest into one analysis-ready shape.

    Values a version never recorded are returned as ``None`` and listed in
    ``provenance_gaps``; they are never filled with a guess. ``deployment_profile``
    is kept as a legacy field so pre-merge sessions stay attributable, and
    ``runtime_config`` stays ``None`` for them instead of borrowing the profile
    name.
    """
    if not isinstance(payload, Mapping):
        raise RuntimeManifestError(
            f"runtime manifest must be a JSON object, got {type(payload).__name__}"
        )
    version = payload.get("schema_version")
    if version is None:
        raise RuntimeManifestError("runtime manifest has no schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise RuntimeManifestError(
            f"runtime manifest schema_version must be an integer, got {version!r}"
        )
    if version not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS:
        raise RuntimeManifestError(
            f"unsupported runtime manifest schema_version {version}; "
            f"this build reads {list(SUPPORTED_MANIFEST_SCHEMA_VERSIONS)}"
        )
    artifact_type = payload.get("artifact_type")
    if artifact_type is not None and artifact_type != MANIFEST_ARTIFACT_TYPE:
        raise RuntimeManifestError(
            f"unexpected artifact_type {artifact_type!r}; expected {MANIFEST_ARTIFACT_TYPE!r}"
        )

    normalized: dict[str, Any] = {field: payload.get(field) for field in _NORMALIZED_FIELDS}
    normalized["schema_version"] = MANIFEST_SCHEMA_VERSION
    normalized["source_schema_version"] = version
    normalized["provenance_gaps"] = [
        field for field in SCHEMA_2_ONLY_FIELDS if normalized.get(field) is None
    ]
    return normalized


def read_runtime_manifest(path: str | Path) -> dict[str, Any]:
    """Load one manifest file and normalize it.

    Read-only: the file is never rewritten, so historical sessions keep the
    exact bytes they were recorded with.
    """
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeManifestError(f"runtime manifest {source} could not be read: {exc}") from exc
    except ValueError as exc:
        raise RuntimeManifestError(f"runtime manifest {source} is not valid JSON: {exc}") from exc
    return normalize_runtime_manifest(payload)
