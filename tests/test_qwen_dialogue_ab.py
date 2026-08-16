"""Deterministic contract tests for the dialogue-model A/B harness."""

from __future__ import annotations

import csv
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


def _write_live_probe_summary(tmp_path: Path, profile_name: str, **overrides) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    profile, models = harness.validate_ab_profile(profile_name)
    payload = {
        "overall_status": "PASS",
        "profile": profile_name,
        "dialogue_model": models.dialogue,
        "hardware_status": "PASS",
        "dialogue_identity_status": "PASS",
        "agent_identity_status": "PASS",
        "agent_inference_status": "PASS",
        "dialogue_stream_status": "PASS",
        "raw_stream_acceptance_status": "PASS",
        "thinking_leak": False,
        "reasoning_field_leak": False,
        "control_tag_leak": False,
        "git_commit": "phase5-test-commit",
    }
    payload.update(overrides)
    path = tmp_path / f"{profile_name}_acceptance_summary.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _run_artifact(tmp_path: Path, profile_name: str, monkeypatch) -> Path:
    _fake_factory(monkeypatch)
    summary = _write_live_probe_summary(tmp_path, profile_name)
    root = tmp_path / profile_name
    assert harness.run_profile(
        profile_name,
        output_root=root,
        timeout_seconds=1,
        live_probe_summary=summary,
    ) == 0
    return next(root.rglob("run.json"))


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


def test_ab_02_expanded_matrix_has_required_categories_and_metadata():
    assert len(harness.SCENARIOS) >= 24
    ids = [scenario.scenario_id for scenario in harness.SCENARIOS]
    assert len(ids) == len(set(ids))
    required_ids = {
        "greeting",
        "low_mood",
        "anxiety",
        "insomnia",
        "loneliness",
        "resistance",
        "repeated_refusal",
        "direct_advice",
        "institutional_frustration",
        "small_talk",
        "gratitude",
        "post_relaxation_no_change",
        "post_relaxation_worse",
        "negation_ambiguity",
        "frequency_ambiguity",
        "duration_ambiguity",
        "quantity_ambiguity",
        "scale_timeframe",
        "scale_frequency",
        "scale_negation",
        "scale_core_symptom",
        "scale_refusal",
        "one_primary_question",
        "avoid_premature_advice",
        "avoid_leading_question",
        "avoid_diagnosis",
        "avoid_motive_attribution",
        "avoid_formulaic_reassurance",
        "closed_environment",
        "long_context",
    }
    assert required_ids <= set(ids)
    assert all(scenario.category for scenario in harness.SCENARIOS)
    assert all(hasattr(scenario, "expected_constraints") for scenario in harness.SCENARIOS)
    assert all(hasattr(scenario, "human_review_dimensions") for scenario in harness.SCENARIOS)
    assert set(harness.REPEATABILITY_SCENARIO_IDS) >= {
        "greeting",
        "resistance",
        "asr_ambiguity",
        "scale_frequency",
        "direct_advice",
        "long_context",
    }


def test_ab_03_scenario_matrix_and_prompt_hash_are_stable(monkeypatch):
    monkeypatch.setattr(harness, "_system_prompt", lambda: "fixed test prompt")
    assert harness.scenario_matrix_hash() == harness.scenario_matrix_hash()
    assert harness.prompt_hash(harness._build_messages(harness.SCENARIOS[0]))


def test_ab_04_phase5_pass_is_required_before_dialogue_requests(tmp_path, monkeypatch):
    clients = _fake_factory(monkeypatch)
    result = harness.run_profile("rtxpro6000_96g", output_root=tmp_path, timeout_seconds=1)
    assert result == 1
    assert clients == {}
    artifact = json.loads(next(tmp_path.rglob("run.json")).read_text(encoding="utf-8"))
    assert artifact["status"] == "FAIL"
    assert "live probe summary" in artifact["errors"][0].lower()


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"overall_status": "FAIL"}, "overall_status"),
        ({"profile": "rtxpro6000_96g_qwen38_candidate"}, "profile"),
        ({"dialogue_model": "wrong-model"}, "dialogue_model"),
        ({"hardware_status": "FAIL"}, "hardware_status"),
        ({"thinking_leak": True}, "thinking_leak"),
        ({"reasoning_field_leak": True}, "reasoning_field_leak"),
        ({"control_tag_leak": True}, "control_tag_leak"),
    ],
)
def test_ab_05_phase5_failures_are_rejected_before_dialogue_requests(
    tmp_path, monkeypatch, overrides, expected
):
    clients = _fake_factory(monkeypatch)
    summary = _write_live_probe_summary(tmp_path, "rtxpro6000_96g", **overrides)
    result = harness.run_profile(
        "rtxpro6000_96g", output_root=tmp_path / "run", timeout_seconds=1, live_probe_summary=summary
    )
    assert result == 1
    assert clients == {}
    artifact = json.loads(next((tmp_path / "run").rglob("run.json")).read_text(encoding="utf-8"))
    assert expected in artifact["errors"][0]


def test_ab_06_run_records_phase5_evidence_and_profile_options(tmp_path, monkeypatch):
    clients = _fake_factory(monkeypatch)
    summary = _write_live_probe_summary(tmp_path, "rtxpro6000_96g")
    result = harness.run_profile(
        "rtxpro6000_96g",
        output_root=tmp_path,
        timeout_seconds=1,
        live_probe_summary=summary,
    )
    assert result == 0
    assert clients["rtxpro6000_96g"].calls
    artifact = json.loads(next(tmp_path.rglob("run.json")).read_text(encoding="utf-8"))
    assert artifact["status"] == "PASS"
    assert artifact["dialogue_model"] == "Qwen/Qwen2.5-72B-Instruct-AWQ"
    assert artifact["scenario_matrix_hash"] == harness.scenario_matrix_hash()
    assert artifact["system_prompt_hash"]
    assert artifact["prompt_hashes"]
    assert artifact["live_probe_profile"] == "rtxpro6000_96g"
    assert artifact["live_probe_dialogue_model"] == artifact["dialogue_model"]
    assert artifact["live_probe_summary_sha256"]
    assert artifact["hardware_validation"] == "PHASE5_PASS_REFERENCED"
    assert artifact["real_ab_run_status"] == "NOT RUN"
    assert artifact["promotion_status"] == "NOT APPROVED"
    assert set(artifact["human_review_dimensions"]) == set(harness.REVIEW_DIMENSIONS)


def test_ab_07_candidate_keeps_thinking_contract_profile_owned(tmp_path, monkeypatch):
    _fake_factory(monkeypatch)
    summary = _write_live_probe_summary(tmp_path, "rtxpro6000_96g_qwen38_candidate")
    result = harness.run_profile(
        "rtxpro6000_96g_qwen38_candidate",
        output_root=tmp_path,
        timeout_seconds=1,
        live_probe_summary=summary,
    )
    assert result == 0
    artifact = json.loads(next(tmp_path.rglob("run.json")).read_text(encoding="utf-8"))
    assert artifact["dialogue_enable_thinking"] is False
    assert artifact["generation_options"]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_ab_08_repeatability_and_performance_are_recorded(tmp_path, monkeypatch):
    clients = _fake_factory(monkeypatch)
    summary = _write_live_probe_summary(tmp_path, "rtxpro6000_96g")
    assert harness.run_profile(
        "rtxpro6000_96g", output_root=tmp_path, timeout_seconds=1, live_probe_summary=summary
    ) == 0
    assert len(clients["rtxpro6000_96g"].calls) == len(harness.SCENARIOS) * 2 + len(
        harness.REPEATABILITY_SCENARIO_IDS
    ) * harness.REPEAT_COUNT
    artifact = json.loads(next(tmp_path.rglob("run.json")).read_text(encoding="utf-8"))
    assert len(artifact["repeatability_results"]) == len(harness.REPEATABILITY_SCENARIO_IDS) * harness.REPEAT_COUNT
    assert {item["repeat_index"] for item in artifact["repeatability_results"]} == {1, 2, 3}
    assert artifact["performance_summary"]["tokens_per_second"] == "NOT AVAILABLE"
    assert artifact["performance_summary"]["request_failures_count"] == 0


def test_ab_09_output_leak_fails_without_stripping(tmp_path, monkeypatch):
    _fake_factory(monkeypatch)
    summary = _write_live_probe_summary(tmp_path, "rtxpro6000_96g")

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
    result = harness.run_profile(
        "rtxpro6000_96g", output_root=tmp_path / "run", timeout_seconds=1, live_probe_summary=summary
    )
    assert result == 1
    artifact = json.loads(next((tmp_path / "run").rglob("run.json")).read_text(encoding="utf-8"))
    assert artifact["status"] == "FAIL"
    assert artifact["leakage_failures"]
    assert "END" in artifact["leakage_failures"][0] or "think" in artifact["leakage_failures"][0]


def test_ab_10_compare_requires_all_input_parity_and_never_promotes(tmp_path, monkeypatch):
    baseline = _run_artifact(tmp_path / "runs", "rtxpro6000_96g", monkeypatch)
    candidate = _run_artifact(tmp_path / "runs", "rtxpro6000_96g_qwen38_candidate", monkeypatch)
    comparison_path = tmp_path / "comparison" / "comparison.json"
    comparison = harness.compare_runs(baseline, candidate, output_path=comparison_path)
    assert comparison["status"] == "READY_FOR_HUMAN_REVIEW"
    assert comparison["promotion_status"] == "NOT APPROVED"
    assert comparison["latency_thresholds"] == "NOT DEFINED"
    assert len(comparison["paired_scenarios"]) == len(harness.SCENARIOS)
    assert set(comparison["human_review_rubric"]) == set(harness.REVIEW_DIMENSIONS)
    assert comparison["hardware_validation"] == "PHASE5_PASS_REFERENCED"
    for key in ("review_packet_A.csv", "review_packet_B.csv", "private_blind_map.json"):
        assert (comparison_path.parent / key).exists()

    packet_a = list(csv.DictReader((comparison_path.parent / "review_packet_A.csv").open(encoding="utf-8")))
    packet_b = list(csv.DictReader((comparison_path.parent / "review_packet_B.csv").open(encoding="utf-8")))
    assert packet_a and packet_b
    assert [row["blind_response_id"] for row in packet_a] != [row["blind_response_id"] for row in packet_b]
    forbidden = ("profile", "model", "baseline", "candidate", "latency", "token", "generation")
    packet_text = (comparison_path.parent / "review_packet_A.csv").read_text(encoding="utf-8").lower()
    assert not any(word in packet_text for word in forbidden)
    private_map = json.loads((comparison_path.parent / "private_blind_map.json").read_text(encoding="utf-8"))
    assert private_map and {"profile", "model", "scenario_id", "repeat_index"} <= set(private_map[0])

    def mutated(name, mutation):
        path = tmp_path / f"{name}.json"
        payload = json.loads(Path(candidate).read_text(encoding="utf-8"))
        mutation(payload)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    with pytest.raises(harness.ABError, match="NOT_COMPARABLE"):
        harness.compare_runs(baseline, mutated("system", lambda p: p.update(system_prompt_hash="different")))
    with pytest.raises(harness.ABError, match="NOT_COMPARABLE"):
        harness.compare_runs(baseline, mutated("scenario_prompt", lambda p: p["prompt_hashes"].update(greeting="different")))
    with pytest.raises(harness.ABError, match="NOT_COMPARABLE"):
        harness.compare_runs(baseline, mutated("commit", lambda p: p.update(git_commit="different")))
    with pytest.raises(harness.ABError, match="INCOMPLETE"):
        harness.compare_runs(
            baseline,
            mutated("failed", lambda p: p.update(status="FAIL")),
        )


def test_ab_11_harness_has_no_service_lifecycle_or_audio_ownership():
    source = Path(harness.__file__).read_text(encoding="utf-8")
    assert "start_blackwell_stack" not in source
    assert "stop_blackwell_stack" not in source
    assert "sounddevice" not in source
    assert "VoxCPM" not in source
    assert "FunASR" not in source
    assert "DataManager" not in source
    assert "conversation_history" not in source
