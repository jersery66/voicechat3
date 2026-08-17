"""Offline integration gate contracts; hardware remains explicitly NOT RUN."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.acceptance import offline_integration


def test_gate_summary_separates_offline_fixture_pass_from_hardware():
    summary = offline_integration.build_gate_summary(returncode=0, git_commit="test-commit")
    assert summary["offline_integration_readiness"] == "PASS"
    assert summary["evidence_type"] == "SIMULATED"
    assert summary["hardware_validation"] == "NOT RUN"
    assert summary["real_stt"] == "NOT RUN"
    assert summary["real_tts"] == "NOT RUN"
    assert summary["real_e2e"] == "NOT RUN"


def test_gate_failure_does_not_claim_hardware_failure_or_success():
    summary = offline_integration.build_gate_summary(returncode=1, git_commit="test-commit")
    assert summary["offline_integration_readiness"] == "FAIL"
    assert summary["hardware_validation"] == "NOT RUN"


def test_gate_targets_only_offline_contracts():
    source = Path(offline_integration.__file__).read_text(encoding="utf-8")
    assert "nvidia-smi" not in source
    assert "wsl.exe" not in source
    assert all("test_offline_integration_readiness.py" not in target for target in offline_integration.OFFLINE_TEST_TARGETS)


def test_gate_writes_provenance_artifact_without_participant_text(tmp_path, monkeypatch):
    monkeypatch.setattr(offline_integration, "run_contract_tests", lambda **_kwargs: 0)
    result = offline_integration.run_gate(output_root=tmp_path, git_commit="test-commit")
    assert result["offline_integration_readiness"] == "PASS"
    artifact = json.loads((tmp_path / "offline_integration_summary.json").read_text(encoding="utf-8"))
    assert artifact["git_commit"] == "test-commit"
    assert artifact["evidence_type"] == "SIMULATED"
    assert "transcript" not in json.dumps(artifact).lower()
