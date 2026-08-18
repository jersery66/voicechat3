"""Per-session runtime provenance without secrets or absolute model paths."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any
from typing import Mapping

from data.atomic_io import atomic_write_json
from deployment.profiles import DeploymentProfile, get_deployment_profile, resolve_runtime_models


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


def build_runtime_manifest(
    profile: DeploymentProfile | None = None,
    *,
    git_sha: str | None = None,
    started_at: str | None = None,
    app_version: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build a configuration/provenance manifest for one session."""
    profile = profile or get_deployment_profile()
    effective_environment = environment
    models = resolve_runtime_models(profile, environment=effective_environment)
    try:
        import config

        system_prompt = profile.system_prompt_override or getattr(config, "SYSTEM_PROMPT", "")
        stt_model = _basename_or_none(getattr(config, "FUNASR_MODEL_PATH", None))
        tts_model = _basename_or_none(getattr(config, "VOXCPM_MODEL_PATH", None))
        app_version = app_version or getattr(config, "APP_VERSION", None) or "UNVERSIONED"
    except Exception:
        system_prompt = ""
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

    if profile.immutable_runtime_contract:
        dialogue_base_url = profile.dialogue_base_url
        agent_base_url = profile.agent_base_url
    else:
        env = effective_environment or {}
        dialogue_base_url = env.get("VOICECHAT_DIALOGUE_BASE_URL", profile.dialogue_base_url)
        agent_base_url = env.get("VOICECHAT_AGENT_BASE_URL", profile.agent_base_url)

    return {
        "schema_version": 1,
        "artifact_type": "SESSION_RUNTIME_MANIFEST",
        "generated_at": started_at or datetime.now(timezone.utc).isoformat(),
        "app_version": app_version,
        "git_sha": git_sha or _current_git_sha(),
        "deployment_profile": profile.name,
        "dialogue_model": models.dialogue,
        "dialogue_base_url": dialogue_base_url,
        "agent_model": models.router,
        "agent_base_url": agent_base_url,
        "stt_model": stt_model,
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
