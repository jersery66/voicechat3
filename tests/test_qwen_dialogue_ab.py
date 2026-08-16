"""Deterministic contract tests for the dialogue-model A/B harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.acceptance import qwen_dialogue_ab as harness


class FakeDialogueClient:
    def __init__(self, profile):
        self.model = profile.dialogue_model
        self.base_url = profile.dialogue_base_url
        self.dialogue_enable_thinking = profile.dialogue_enable_thinking
        self._profile = profile
        self.calls = []

    def list_model_ids(self):
        return [self.model]

    def _generation_options(self, requested_max_tokens=None):
        return {
            "temperature": self._profile.dialogue_temperature,
            "top_p": self._profile.dialogue_top_p,
            "max_tokens": requested_max_tokens or self._profile.dialogue_max_tokens,
            "extra_body": (
                {
                    "top_k": self._profile.dialogue_top_k,
                    "chat_template_kwargs": {"enable_thinking": False},
                }
                if self._profile.dialogue_top_k is not None
                else {}
            ),
        }

    def complete_messages(self, *, messages, max_tokens):
        self.calls.append(("complete", messages, max_tokens))
        return f"{self._profile.name} 的简短回应。"

    def stream_messages(self, *, messages):
        self.calls.append(("stream", messages))
        yield f"{self._profile.name} 的"
        yield "流式回应。"


def _fake_factory(monkeypatch):
    clients = {}

    def build(profile, models, *, timeout):
        client = FakeDialogueClient(profile)
        clients[profile.name] = client
        return client

    monkeypatch.setattr(harness, "build_dialogue_client", build)
    monkeypatch.setattr(harness, "_system_prompt", lambda: "fixed test prompt")
    return clients


def test_ab_01_only_explicit_blackwell_profiles_are_supported():
    assert harness.SUPPORTED_PROFILES == {
        "rtxpro6000_96g",
        "rtxpro6000_96g_qwen38_candidate",
    }
    for name in harness.SUPPORTED_PROFILES:
        profile, models = harness.validate_ab_profile(name)
        assert profile.name == name
        assert models.dialogue == profile.dialogue_model
    for name in ("dev_6g", "dev_vllm_6g", "a100_80g", "a100_80g_qwen38_candidate"):
        with pytest.raises(harness.ABError):
            harness.validate_ab_profile(name)


def test_ab_02_scenario_matrix_and_prompt_hash_are_stable():
    assert len(harness.SCENARIOS) >= 5
    ids = [scenario.scenario_id for scenario in harness.SCENARIOS]
    assert len(ids) == len(set(ids))
    assert harness.scenario_matrix_hash() == harness.scenario_matrix_hash()
    assert harness.prompt_hash(harness._build_messages(harness.SCENARIOS[0]))


def test_ab_03_run_uses_profile_factory_and_records_profile_options(tmp_path, monkeypatch):
    clients = _fake_factory(monkeypatch)
    result = harness.run_profile("rtxpro6000_96g", output_root=tmp_path, timeout_seconds=1)
    assert result == 0
    assert clients["rtxpro6000_96g"].calls
    artifact = json.loads(next(tmp_path.rglob("run.json")).read_text(encoding="utf-8"))
    assert artifact["status"] == "PASS"
    assert artifact["dialogue_model"] == "Qwen/Qwen2.5-72B-Instruct-AWQ"
    assert artifact["scenario_matrix_hash"] == harness.scenario_matrix_hash()
    assert artifact["prompt_hash"]
    assert artifact["promotion_status"] == "NOT APPROVED"
    assert set(artifact["human_review_dimensions"]) == set(harness.REVIEW_DIMENSIONS)


def test_ab_04_candidate_keeps_thinking_contract_profile_owned(tmp_path, monkeypatch):
    _fake_factory(monkeypatch)
    result = harness.run_profile(
        "rtxpro6000_96g_qwen38_candidate", output_root=tmp_path, timeout_seconds=1
    )
    assert result == 0
    artifact = json.loads(next(tmp_path.rglob("run.json")).read_text(encoding="utf-8"))
    assert artifact["dialogue_enable_thinking"] is False
    assert artifact["generation_options"]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_ab_05_output_leak_fails_without_stripping(tmp_path, monkeypatch):
    _fake_factory(monkeypatch)

    class LeakingClient(FakeDialogueClient):
        def complete_messages(self, *, messages, max_tokens):
            return "[END_QUIT]"

        def stream_messages(self, *, messages):
            yield "<think>hidden</think>"

    monkeypatch.setattr(
        harness,
        "build_dialogue_client",
        lambda profile, models, *, timeout: LeakingClient(profile),
    )
    result = harness.run_profile("rtxpro6000_96g", output_root=tmp_path, timeout_seconds=1)
    assert result == 1
    artifact = json.loads(next(tmp_path.rglob("run.json")).read_text(encoding="utf-8"))
    assert artifact["status"] == "FAIL"
    assert artifact["leakage_failures"]
    assert "END" in artifact["leakage_failures"][0] or "think" in artifact["leakage_failures"][0]


def test_ab_06_compare_requires_matching_scenario_matrix_and_never_promotes(tmp_path, monkeypatch):
    _fake_factory(monkeypatch)
    assert harness.run_profile("rtxpro6000_96g", output_root=tmp_path / "baseline", timeout_seconds=1) == 0
    assert harness.run_profile(
        "rtxpro6000_96g_qwen38_candidate", output_root=tmp_path / "candidate", timeout_seconds=1
    ) == 0
    baseline = next((tmp_path / "baseline").rglob("run.json"))
    candidate = next((tmp_path / "candidate").rglob("run.json"))
    comparison = harness.compare_runs(baseline, candidate, output_path=tmp_path / "comparison.json")
    assert comparison["status"] == "READY_FOR_HUMAN_REVIEW"
    assert comparison["promotion_status"] == "NOT APPROVED"
    assert comparison["latency_thresholds"] == "NOT DEFINED"
    assert len(comparison["paired_scenarios"]) == len(harness.SCENARIOS)
    assert set(comparison["human_review_rubric"]) == set(harness.REVIEW_DIMENSIONS)


def test_ab_07_harness_has_no_service_lifecycle_or_audio_ownership():
    source = Path(harness.__file__).read_text(encoding="utf-8")
    assert "start_blackwell_stack" not in source
    assert "stop_blackwell_stack" not in source
    assert "sounddevice" not in source
    assert "VoxCPM" not in source
    assert "FunASR" not in source
