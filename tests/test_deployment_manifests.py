"""Offline contracts for derived deployment/acceptance manifests."""

from __future__ import annotations

import json
import subprocess
import sys

from deployment.profiles import get_deployment_profile, resolve_runtime_models
from scripts.deployment import manifest


def test_deployment_manifest_is_derived_from_baseline_profile():
    profile = get_deployment_profile("rtxpro6000_96g")
    models = resolve_runtime_models(profile, environment={})
    result = manifest.build_deployment_manifest(
        profile.name,
        generated_at="2026-08-17T00:00:00+00:00",
        git_commit="test-commit",
    )

    assert result["artifact_type"] == "DEPLOYMENT_EXPECTATION"
    assert result["evidence_type"] == "CONFIGURATION"
    assert result["source_of_truth"] == "deployment.profiles.DeploymentProfile"
    assert result["profile"] == profile.name
    assert result["git_commit"] == "test-commit"
    assert result["dialogue"]["model"] == profile.dialogue_model
    assert result["dialogue"]["resolved_model"] == models.dialogue
    assert result["dialogue"]["endpoint"]["url"] == profile.dialogue_base_url
    assert result["agent"]["model"] == profile.agent_model
    assert result["agent"]["resolved_model"] == models.router
    assert result["agent"]["expected_port"] == 8001


def test_candidate_manifest_keeps_candidate_identity_and_nonthinking_contract():
    profile = get_deployment_profile("rtxpro6000_96g_qwen38_candidate")
    result = manifest.build_deployment_manifest(profile.name, git_commit="test-commit")

    assert result["dialogue"]["model"] == "Qwen/Qwen3.8-27B-FP8"
    assert result["dialogue"]["generation_contract"]["enable_thinking"] is False
    assert result["dialogue"]["expected_port"] == 8000
    assert result["agent"]["model"] == "Qwen/Qwen2.5-3B-Instruct-AWQ"
    assert result["profile"] != "rtxpro6000_96g"


def test_manifests_contain_no_measured_hardware_result():
    deployment = manifest.build_deployment_manifest("rtxpro6000_96g", git_commit="test-commit")
    acceptance = manifest.build_acceptance_manifest("rtxpro6000_96g", git_commit="test-commit")

    assert deployment["measured_hardware"] == "NOT DECLARED"
    assert deployment["measured_vram"] == "NOT DECLARED"
    assert deployment["measured_latency"] == "NOT DECLARED"
    assert acceptance["offline_readiness"]["status"] == "NOT RUN"
    assert all(value["status"] == "NOT RUN" for value in acceptance["checks"].values())
    assert acceptance["promotion"]["status"] == "NOT APPROVED"
    assert acceptance["promotion_status"] == "NOT APPROVED"
    for slot in acceptance["measurement_evidence_slots"].values():
        assert slot == {"status": "NOT RUN", "evidence_reference": None}


def test_write_manifests_records_both_artifacts_and_provenance(tmp_path):
    deployment_path, acceptance_path = manifest.write_manifests(
        "rtxpro6000_96g",
        output_root=tmp_path,
        generated_at="2026-08-17T00:00:00+00:00",
        git_commit="test-commit",
    )
    deployment = json.loads(deployment_path.read_text(encoding="utf-8"))
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))

    assert deployment_path.name == "deployment_manifest.json"
    assert acceptance_path.name == "acceptance_manifest.json"
    assert deployment["git_commit"] == acceptance["git_commit"] == "test-commit"
    assert deployment["profile"] == acceptance["profile"] == "rtxpro6000_96g"


def test_manifest_cli_supports_direct_script_invocation(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(manifest.PROJECT_ROOT / "scripts" / "deployment" / "manifest.py"),
            "--profile",
            "rtxpro6000_96g",
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
    production_files = [root / "main.py", root / "config.py", root / "deployment" / "profiles.py"]
    for path in production_files:
        assert "deployment.manifest" not in path.read_text(encoding="utf-8")
