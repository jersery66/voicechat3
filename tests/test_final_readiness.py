"""Contracts for the final pre-hardware readiness gate."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.deployment import final_readiness


def test_final_gate_pass_is_offline_only_and_hardware_not_run():
    summary = final_readiness.build_final_summary(
        test_returncode=0,
        documentation_ok=True,
        git_commit="test-commit",
    )
    assert summary["pre_hardware_development"] == "COMPLETE"
    assert summary["pre_hardware_final_readiness"] == "PASS"
    assert summary["offline_evidence"] == "SIMULATED / CONTRACT"
    assert summary["hardware_validation"] == "NOT RUN"
    assert summary["real_gpu"] == "NOT RUN"
    assert summary["real_vllm"] == "NOT RUN"
    assert summary["real_phase5"] == "NOT RUN"
    assert summary["real_ab"] == "NOT RUN"
    assert summary["real_stt"] == "NOT RUN"
    assert summary["real_tts"] == "NOT RUN"
    assert summary["real_e2e"] == "NOT RUN"
    assert summary["candidate_promotion"] == "NOT APPROVED"


def test_missing_required_document_blocks_final_gate():
    summary = final_readiness.build_final_summary(
        test_returncode=0,
        documentation_ok=False,
        git_commit="test-commit",
    )
    assert summary["pre_hardware_final_readiness"] == "FAIL"
    assert summary["pre_hardware_development"] == "INCOMPLETE"
    assert summary["hardware_validation"] == "NOT RUN"


def test_test_failure_blocks_gate_without_claiming_hardware_failure():
    summary = final_readiness.build_final_summary(
        test_returncode=1,
        documentation_ok=True,
        git_commit="test-commit",
    )
    assert summary["pre_hardware_final_readiness"] == "FAIL"
    assert summary["hardware_validation"] == "NOT RUN"


def test_required_docs_and_tools_are_declared():
    assert "docs/deployment/rtxpro6000_operator_runbook.md" in final_readiness.REQUIRED_DOCUMENTS
    assert "scripts/deployment/doctor.py" in final_readiness.REQUIRED_TOOLS
    assert "scripts/acceptance/blackwell_live_probe.py" in final_readiness.REQUIRED_TOOLS


def test_run_final_gate_writes_provenance_artifact_without_hardware_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(final_readiness, "run_full_regression", lambda: 0)
    monkeypatch.setattr(final_readiness, "documentation_status", lambda: (True, []))
    summary = final_readiness.run_final_gate(output_root=tmp_path, git_commit="test-commit")
    artifact = json.loads((tmp_path / "pre_hardware_final_readiness.json").read_text(encoding="utf-8"))
    assert summary["pre_hardware_final_readiness"] == "PASS"
    assert artifact["git_commit"] == "test-commit"
    assert artifact["hardware_validation"] == "NOT RUN"
    source = Path(final_readiness.__file__).read_text(encoding="utf-8")
    assert "nvidia-smi" not in source
    assert "wsl.exe" not in source
