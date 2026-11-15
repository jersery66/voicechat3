"""Deterministic software-only Relaxation Center V1 preflight."""

from __future__ import annotations

from scripts.relaxation_center_v1_preflight import run_preflight


def test_software_preflight_checks_catalog_imports_and_licensing_without_hardware_claims():
    result = run_preflight()

    assert result["overall_status"] == "PASS"
    assert result["hardware_validation"] == "NOT RUN"
    assert result["real_media_playback"] == "NOT RUN"
    assert result["catalog"]["core_ids"] == ["breathing", "muscle_relaxation", "meditation"]
    assert result["catalog"]["leisure_game_ids"] == [
        "bubble_pop", "gentle_search", "calm_puzzle", "falling_leaves"
    ]
    assert result["core_resource_existence"] == "NOT VERIFIED"
    assert result["third_party_notices"] == "PASS"


def test_software_preflight_does_not_report_gpu_or_vllm_as_pass():
    result = run_preflight()

    assert result["gpu"] == "NOT RUN"
    assert result["cuda"] == "NOT RUN"
    assert result["vllm"] == "NOT RUN"
    assert result["agent"] == "NOT RUN"
    assert result["dialogue"] == "NOT RUN"
