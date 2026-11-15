"""Offline contracts for derived deployment/acceptance manifests."""

from __future__ import annotations

import json
import subprocess
import sys
from urllib.parse import urlparse

from deployment.runtime_config import get_runtime_config
from scripts.deployment import manifest


def test_deployment_manifest_is_derived_from_the_runtime_configuration():
    config = get_runtime_config()
    result = manifest.build_deployment_manifest(
        generated_at="2026-08-17T00:00:00+00:00",
        git_commit="test-commit",
    )

    assert result["artifact_type"] == "DEPLOYMENT_EXPECTATION"
    assert result["evidence_type"] == "CONFIGURATION"
    assert result["source_of_truth"] == "deployment.runtime_config.RuntimeConfig"
    assert result["runtime_config"] == str(config.source_path)
    assert result["git_commit"] == "test-commit"
    assert result["dialogue"]["model"] == config.dialogue.model
    assert result["dialogue"]["provider"] == config.dialogue.provider
    assert result["dialogue"]["endpoint"]["url"] == config.dialogue.base_url
    assert result["dialogue"]["expected_port"] == urlparse(config.dialogue.base_url).port
    assert result["agent"]["model"] == config.agent.model
    assert result["agent"]["enabled"] is config.agent.enabled


def test_manifest_records_the_configured_voice_providers():
    config = get_runtime_config()
    result = manifest.build_deployment_manifest(git_commit="test-commit")

    assert result["asr"]["provider"] == config.asr.provider
    assert result["tts"]["provider"] == config.tts.provider
    assert result["dialogue"]["generation_contract"]["enable_thinking"] == config.dialogue.enable_thinking


def test_manifests_contain_no_measured_hardware_result():
    deployment = manifest.build_deployment_manifest(git_commit="test-commit")
    acceptance = manifest.build_acceptance_manifest(git_commit="test-commit")

    assert deployment["measured_hardware"] == "NOT DECLARED"
    assert deployment["measured_vram"] == "NOT DECLARED"
    assert deployment["measured_latency"] == "NOT DECLARED"
    assert acceptance["offline_readiness"]["status"] == "NOT RUN"
    assert acceptance["offline_integration_readiness"] == {"status": "NOT RUN", "evidence_reference": None}
    assert all(value["status"] == "NOT RUN" for value in acceptance["checks"].values())
    assert acceptance["promotion"]["status"] == "NOT APPROVED"
    assert acceptance["promotion_status"] == "NOT APPROVED"
    for slot in acceptance["measurement_evidence_slots"].values():
        assert slot == {"status": "NOT RUN", "evidence_reference": None}


def test_write_manifests_records_both_artifacts_and_provenance(tmp_path):
    deployment_path, acceptance_path = manifest.write_manifests(
        output_root=tmp_path,
        generated_at="2026-08-17T00:00:00+00:00",
        git_commit="test-commit",
    )
    deployment = json.loads(deployment_path.read_text(encoding="utf-8"))
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))

    assert deployment_path.name == "deployment_manifest.json"
    assert acceptance_path.name == "acceptance_manifest.json"
    assert deployment["git_commit"] == acceptance["git_commit"] == "test-commit"
    assert deployment["runtime_config"] == acceptance["runtime_config"] == str(get_runtime_config().source_path)


def test_manifest_cli_supports_direct_script_invocation(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(manifest.PROJECT_ROOT / "scripts" / "deployment" / "manifest.py"),
            "--output-root",
            str(tmp_path),
        ],
        cwd=manifest.PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert (tmp_path / "deployment_manifest.json").exists()
    assert (tmp_path / "acceptance_manifest.json").exists()


def test_manifest_module_is_not_consumed_by_production_runtime():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    production_files = [root / "main.py", root / "config.py", root / "deployment" / "runtime_config.py"]
    for path in production_files:
        assert "deployment.manifest" not in path.read_text(encoding="utf-8")
