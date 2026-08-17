"""Deterministic evidence-state tests for the real hardware preflight."""

from __future__ import annotations

from scripts import real_hardware_preflight as preflight


def test_measured_preflight_success_is_measured_and_pass():
    metadata = preflight._evidence_metadata(
        {
            "windows_gpu": {"status": "PASS"},
            "wsl_gpu": {"status": "PASS"},
            "torch_cuda": {"status": "PASS"},
            "vllm_version": {"status": "PASS"},
            "windows_wsl_gpu_consistency": {"status": "PASS"},
        },
        overall_status="PASS",
    )

    assert metadata == {
        "execution": "RAN",
        "evidence_type": "MEASURED",
        "status": "PASS",
        "hardware_validation": "PASS",
    }


def test_measured_preflight_failure_stays_measured_and_fail():
    metadata = preflight._evidence_metadata(
        {
            "windows_gpu": {"status": "PASS"},
            "wsl_gpu": {"status": "PASS"},
            "torch_cuda": {"status": "FAIL", "error": "CUDA unavailable"},
            "vllm_version": {"status": "PASS"},
            "windows_wsl_gpu_consistency": {"status": "PASS"},
        },
        overall_status="FAIL",
    )

    assert metadata["execution"] == "RAN"
    assert metadata["evidence_type"] == "MEASURED"
    assert metadata["status"] == "FAIL"
    assert metadata["hardware_validation"] == "FAIL"


def test_unavailable_preflight_is_not_run_evidence_not_measured_failure():
    metadata = preflight._evidence_metadata(
        {
            "windows_gpu": {"status": "NOT AVAILABLE"},
            "wsl_gpu": {"status": "NOT AVAILABLE"},
            "torch_cuda": {"status": "NOT RUN"},
            "vllm_version": {"status": "NOT RUN"},
            "windows_wsl_gpu_consistency": {"status": "NOT RUN"},
        },
        overall_status="FAIL",
    )

    assert metadata["execution"] == "RAN"
    assert metadata["evidence_type"] == "NOT AVAILABLE"
    assert metadata["status"] == "FAIL"
    assert metadata["hardware_validation"] == "NOT RUN"
