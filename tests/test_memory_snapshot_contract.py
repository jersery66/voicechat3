"""Offline contracts for GPU memory snapshot collection."""

from __future__ import annotations

import subprocess

from scripts.deployment.memory_snapshot import (
    EVIDENCE_NOT_AVAILABLE,
    EVIDENCE_SIMULATED,
    capture_memory_snapshot,
    parse_nvidia_smi_csv,
    simulated_memory_snapshot,
)


CSV = """0, NVIDIA RTX PRO 6000 Blackwell Workstation Edition, 98304, 1024, 97280\n"""


def test_nvidia_smi_parser_is_deterministic():
    rows = parse_nvidia_smi_csv(CSV)
    assert rows == [
        {
            "gpu_index": 0,
            "gpu_name": "NVIDIA RTX PRO 6000 Blackwell Workstation Edition",
            "memory_total_mb": 98304,
            "memory_used_mb": 1024,
            "memory_free_mb": 97280,
        }
    ]


def test_nvidia_smi_parser_preserves_unavailable_fields():
    rows = parse_nvidia_smi_csv("0, GPU, N/A, N/A, N/A\n")
    assert rows[0]["memory_total_mb"] is None
    assert rows[0]["memory_used_mb"] is None
    assert rows[0]["memory_free_mb"] is None


def test_capture_without_nvidia_smi_is_not_available():
    def missing(_command, **_kwargs):
        raise FileNotFoundError("nvidia-smi")

    result = capture_memory_snapshot(runner=missing, profile="rtxpro6000_96g")
    assert result["status"] == "NOT AVAILABLE"
    assert result["evidence_type"] == EVIDENCE_NOT_AVAILABLE
    assert result["hardware_evidence"] == "NOT RUN"
    assert result["memory_total_mb"] is None


def test_capture_records_measured_rows_without_thresholds():
    def runner(command, **_kwargs):
        assert "nvidia-smi" in command[0]
        return subprocess.CompletedProcess(command, 0, CSV, "")

    result = capture_memory_snapshot(runner=runner, profile="rtxpro6000_96g", git_commit="test-commit")
    assert result["status"] == "PASS"
    assert result["evidence_type"] == "MEASURED"
    assert result["git_commit"] == "test-commit"
    assert result["memory_total_mb"] == 98304
    assert result["processes"] is None
    assert result["processes_status"] == "NOT AVAILABLE"
    assert "threshold" not in result


def test_simulated_snapshot_is_explicitly_simulated():
    result = simulated_memory_snapshot(
        {"gpu_index": 0, "gpu_name": "fake", "memory_total_mb": 10, "memory_used_mb": 2, "memory_free_mb": 8},
        profile="rtxpro6000_96g",
    )
    assert result["status"] == "PASS"
    assert result["evidence_type"] == EVIDENCE_SIMULATED
    assert result["hardware_evidence"] == "NOT RUN"
