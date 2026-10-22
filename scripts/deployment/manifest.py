"""Generate derived deployment and acceptance manifests.

The manifests are evidence/expectation artifacts only.  Production startup
continues to resolve its models and endpoints from the runtime
configuration (``config/runtime.yaml``) and never reads these generated
JSON files as configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deployment.runtime_config import RuntimeConfig, get_runtime_config, load_runtime_config
from scripts.deployment.doctor import REQUIRED_FILES

DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "test_output" / "deployment_readiness"


def _resolve_config(config_path: str | None) -> RuntimeConfig:
    if config_path:
        return load_runtime_config(config_path)
    return get_runtime_config()


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _endpoint(base_url: str) -> dict[str, Any]:
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    return {
        "url": base_url,
        "host": parsed.hostname,
        "port": parsed.port,
        "models_path": f"{base_url.rstrip('/')}/models",
    }


def _generation_contract(config: RuntimeConfig) -> dict[str, Any]:
    dialogue = config.dialogue
    return {
        "request_mode": dialogue.request_mode,
        "system_role_mode": dialogue.system_role_mode,
        "max_tokens": dialogue.max_tokens,
        "temperature": dialogue.temperature,
        "top_p": dialogue.top_p,
        "top_k": dialogue.top_k,
        "presence_penalty": dialogue.presence_penalty,
        "enable_thinking": dialogue.enable_thinking,
    }


def build_deployment_manifest(
    config_path: str | None = None,
    *,
    generated_at: str | None = None,
    git_commit: str | None = None,
) -> dict[str, Any]:
    config = _resolve_config(config_path)
    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": 1,
        "artifact_type": "DEPLOYMENT_EXPECTATION",
        "evidence_type": "CONFIGURATION",
        "generated_at": timestamp,
        "generator": "scripts/deployment/manifest.py",
        "source_of_truth": "deployment.runtime_config.RuntimeConfig",
        "git_commit": git_commit if git_commit is not None else _git_commit(),
        "runtime_config": str(config.source_path),
        "dialogue": {
            "provider": config.dialogue.provider,
            "model": config.dialogue.model,
            "endpoint": _endpoint(config.dialogue.base_url),
            "expected_port": _endpoint(config.dialogue.base_url)["port"],
            "generation_contract": _generation_contract(config),
        },
        "agent": {
            "enabled": config.agent.enabled,
            "provider": config.agent.provider,
            "model": config.agent.model,
            "endpoint": _endpoint(config.agent.base_url),
            "expected_port": _endpoint(config.agent.base_url)["port"],
        },
        "asr": {
            "provider": config.asr.provider,
            "configured_model_reference": "config.FUNASR_MODEL_PATH",
        },
        "tts": {
            "provider": config.tts.provider,
            "configured_model_reference": "config.VOXCPM_MODEL_PATH"
            if config.tts.provider == "voxcpm"
            else "config.COSYVOICE_MODEL_PATH",
        },
        "strict_preflight": config.runtime.strict_preflight,
        "immutable_runtime_contract": config.runtime.immutable_contract,
        "required_repository_files": list(REQUIRED_FILES),
        "acceptance_tools": {
            "deployment_doctor": "scripts/deployment/doctor.py",
            "phase5_live_probe": "scripts/acceptance/blackwell_live_probe.py",
            "dialogue_ab": "scripts/acceptance/qwen_dialogue_ab.py",
        },
        "measured_hardware": "NOT DECLARED",
        "measured_vram": "NOT DECLARED",
        "measured_latency": "NOT DECLARED",
    }


ACCEPTANCE_STATES = {
    "target_hardware": "NOT RUN",
    "windows_gpu_identity": "NOT RUN",
    "wsl_gpu_visibility": "NOT RUN",
    "cuda": "NOT RUN",
    "inference_engine": "NOT RUN",
    "agent_identity": "NOT RUN",
    "dialogue_identity": "NOT RUN",
    "phase5": "NOT RUN",
    "dialogue_ab": "NOT RUN",
    "human_review": "NOT RUN",
    "stt": "NOT RUN",
    "tts": "NOT RUN",
    "stt_llm_tts_e2e": "NOT RUN",
    "continuous_stability": "NOT RUN",
}

MEASUREMENT_EVIDENCE_SLOTS = {
    "performance_measurement": {"status": "NOT RUN", "evidence_reference": None},
    "memory_measurement": {"status": "NOT RUN", "evidence_reference": None},
    "e2e_timing": {"status": "NOT RUN", "evidence_reference": None},
}


def build_acceptance_manifest(
    config_path: str | None = None,
    *,
    generated_at: str | None = None,
    git_commit: str | None = None,
) -> dict[str, Any]:
    # Resolve through the same runtime configuration so an acceptance plan
    # cannot silently describe a different model than the deployment
    # expectation.
    config = _resolve_config(config_path)
    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": 1,
        "artifact_type": "ACCEPTANCE_STATUS_MATRIX",
        "evidence_type": "ACCEPTANCE_PLAN",
        "generated_at": timestamp,
        "generator": "scripts/deployment/manifest.py",
        "source_of_truth": "deployment.runtime_config.RuntimeConfig",
        "git_commit": git_commit if git_commit is not None else _git_commit(),
        "runtime_config": str(config.source_path),
        "dialogue_model": config.dialogue.model,
        "agent_model": config.agent.model,
        "offline_readiness": {"status": "NOT RUN"},
        "offline_integration_readiness": {"status": "NOT RUN", "evidence_reference": None},
        **{name: {"status": status} for name, status in ACCEPTANCE_STATES.items()},
        "checks": {name: {"status": status} for name, status in ACCEPTANCE_STATES.items()},
        "measurement_evidence_slots": dict(MEASUREMENT_EVIDENCE_SLOTS),
        "promotion": {"status": "NOT APPROVED"},
        "promotion_status": "NOT APPROVED",
        "evidence_references": {},
    }


def write_manifests(
    config_path: str | None = None,
    *,
    output_root: str | Path | None = None,
    generated_at: str | None = None,
    git_commit: str | None = None,
) -> tuple[Path, Path]:
    root = Path(output_root) if output_root is not None else DEFAULT_OUTPUT_ROOT
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    root.mkdir(parents=True, exist_ok=True)
    deployment_path = root / "deployment_manifest.json"
    acceptance_path = root / "acceptance_manifest.json"
    deployment_path.write_text(
        json.dumps(
            build_deployment_manifest(config_path, generated_at=generated_at, git_commit=git_commit),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    acceptance_path.write_text(
        json.dumps(
            build_acceptance_manifest(config_path, generated_at=generated_at, git_commit=git_commit),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return deployment_path, acceptance_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate derived deployment and acceptance manifests")
    parser.add_argument("--config", default=os.environ.get("VOICECHAT_RUNTIME_CONFIG"))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args(argv)
    deployment_path, acceptance_path = write_manifests(args.config, output_root=args.output_root)
    print(json.dumps({"deployment_manifest": str(deployment_path), "acceptance_manifest": str(acceptance_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
