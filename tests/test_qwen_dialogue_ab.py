"""Deterministic contract tests for the dialogue-model A/B harness."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from deployment.runtime_config import PROVIDER_OPENAI_COMPATIBLE
from scripts.acceptance import qwen_dialogue_ab as harness


BASELINE_MODEL = "Qwen/Qwen2.5-72B-Instruct-AWQ"
CANDIDATE_MODEL = "Qwen/Qwen3.8-27B-FP8"

CONFIG_TEMPLATE = """
dialogue:
  provider: openai_compatible
  base_url: http://127.0.0.1:8000/v1
  model: {model}
{extra}agent:
  enabled: false
asr:
  provider: funasr
tts:
  provider: voxcpm
runtime:
  strict_preflight: true
  immutable_contract: true
"""


class FakeDialogueClient:
    def __init__(self, config):
        self.model = config.dialogue.model
        self.base_url = config.dialogue.base_url
        self.dialogue_enable_thinking = config.dialogue.enable_thinking
        self._config = config
        self.calls = []

    def list_model_ids(self):
        return [self.model]

    def _generation_options(self, requested_max_tokens=None):
        extra_body: dict = {}
        if self._config.dialogue.top_k is not None:
            extra_body["top_k"] = self._config.dialogue.top_k
        if self._config.dialogue.enable_thinking is False:
            extra_body["chat_template_kwargs"] = {"enable_thinking": False}
        return {
            "temperature": self._config.dialogue.temperature,
            "top_p": self._config.dialogue.top_p,
            "max_tokens": requested_max_tokens or self._config.dialogue.max_tokens,
            "extra_body": extra_body,
        }

    def complete_messages(self, *, messages, max_tokens):
        self.calls.append(("complete", messages, max_tokens))
        return "固定测试回应。"

    def stream_messages(self, *, messages):
        self.calls.append(("stream", messages))
        yield "固定测试"
        yield "流式回应。"


def _fake_factory(monkeypatch):
    clients = {}

    def build(config=None, *, timeout=None):
        client = FakeDialogueClient(config)
        clients[config.source_path] = client
        return client

    monkeypatch.setattr(harness, "build_dialogue_client", build)
    monkeypatch.setattr(harness, "_system_prompt", lambda: "fixed test prompt")
    return clients


def _write_config(tmp_path: Path, name: str, *, model: str, extra: str = "") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / name
    path.write_text(CONFIG_TEMPLATE.format(model=model, extra=extra), encoding="utf-8")
    return path


def _baseline_config(tmp_path: Path) -> Path:
    return _write_config(tmp_path, "runtime_baseline.yaml", model=BASELINE_MODEL)


def _candidate_config(tmp_path: Path) -> Path:
    return _write_config(
        tmp_path,
        "runtime_candidate.yaml",
        model=CANDIDATE_MODEL,
        extra="  enable_thinking: false\n",
    )


def _write_live_probe_summary(
    tmp_path: Path, runtime_config: str = "runtime_baseline.yaml", dialogue_model: str = BASELINE_MODEL, **overrides
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    payload = {
        "overall_status": "PASS",
        "runtime_config": runtime_config,
        "dialogue_model": dialogue_model,
        "hardware_status": "PASS",
        "dialogue_identity_status": "PASS",
        "agent_identity_status": "SKIPPED",
        "agent_inference_status": "SKIPPED",
        "dialogue_stream_status": "PASS",
        "raw_stream_acceptance_status": "PASS",
        "thinking_leak": False,
        "reasoning_field_leak": False,
        "control_tag_leak": False,
        "git_commit": "phase5-test-commit",
    }
    payload.update(overrides)
    path = tmp_path / f"{runtime_config}_acceptance_summary.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _run_artifact(tmp_path: Path, config_path: Path, dialogue_model: str, monkeypatch) -> Path:
    _fake_factory(monkeypatch)
    summary = _write_live_probe_summary(tmp_path, config_path.name, dialogue_model)
    root = tmp_path / config_path.stem
    assert harness.run_config(
        config_path,
        output_root=root,
        timeout_seconds=1,
        live_probe_summary=summary,
    ) == 0
    return next(root.rglob("run.json"))


def test_ab_01_runtime_configuration_is_the_only_ab_selection():
    config = harness.load_ab_config(None)
    assert config.dialogue.provider == PROVIDER_OPENAI_COMPATIBLE
    assert config.runtime.immutable_contract is True
    assert config.runtime.strict_preflight is True


def test_ab_01b_non_immutable_configuration_is_rejected(tmp_path):
    path = _write_config(tmp_path, "mutable.yaml", model=BASELINE_MODEL)
    text = path.read_text(encoding="utf-8").replace("immutable_contract: true", "immutable_contract: false")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(harness.ABError):
        harness.load_ab_config(path)


def test_ab_01c_remote_endpoint_configuration_is_rejected(tmp_path):
    path = _write_config(tmp_path, "remote.yaml", model=BASELINE_MODEL)
    text = path.read_text(encoding="utf-8").replace(
        "base_url: http://127.0.0.1:8000/v1", "base_url: http://192.168.1.10:8000/v1"
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(harness.ABError):
        harness.load_ab_config(path)


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
    config_path = _baseline_config(tmp_path)
    result = harness.run_config(config_path, output_root=tmp_path, timeout_seconds=1)
    assert result == 1
    assert clients == {}
    artifact = json.loads(next(tmp_path.rglob("run.json")).read_text(encoding="utf-8"))
    assert artifact["status"] == "FAIL"
    assert "live probe summary" in artifact["errors"][0].lower()


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"overall_status": "FAIL"}, "overall_status"),
        ({"runtime_config": "runtime_candidate.yaml"}, "runtime_config"),
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
    config_path = _baseline_config(tmp_path)
    summary = _write_live_probe_summary(tmp_path, **overrides)
    result = harness.run_config(
        config_path, output_root=tmp_path / "run", timeout_seconds=1, live_probe_summary=summary
    )
    assert result == 1
    assert clients == {}
    artifact = json.loads(next((tmp_path / "run").rglob("run.json")).read_text(encoding="utf-8"))
    assert expected in artifact["errors"][0]


def test_ab_06_run_records_phase5_evidence_and_configuration_options(tmp_path, monkeypatch):
    clients = _fake_factory(monkeypatch)
    config_path = _baseline_config(tmp_path)
    summary = _write_live_probe_summary(tmp_path, config_path.name, BASELINE_MODEL)
    result = harness.run_config(
        config_path,
        output_root=tmp_path,
        timeout_seconds=1,
        live_probe_summary=summary,
    )
    assert result == 0
    client = next(iter(clients.values()))
    assert client.calls
    artifact = json.loads(next(tmp_path.rglob("run.json")).read_text(encoding="utf-8"))
    assert artifact["status"] == "PASS"
    assert artifact["runtime_config"] == "runtime_baseline.yaml"
    assert artifact["dialogue_model"] == BASELINE_MODEL
    assert artifact["scenario_matrix_hash"] == harness.scenario_matrix_hash()
    assert artifact["system_prompt_hash"]
    assert artifact["prompt_hashes"]
    assert artifact["live_probe_runtime_config"] == "runtime_baseline.yaml"
    assert artifact["live_probe_dialogue_model"] == artifact["dialogue_model"]
    assert artifact["live_probe_summary_sha256"]
    assert artifact["hardware_validation"] == "PHASE5_PASS_REFERENCED"
    assert artifact["real_ab_run_status"] == "NOT RUN"
    assert artifact["promotion_status"] == "NOT APPROVED"
    assert set(artifact["human_review_dimensions"]) == set(harness.REVIEW_DIMENSIONS)


def test_ab_07_candidate_keeps_thinking_contract_configuration_owned(tmp_path, monkeypatch):
    _fake_factory(monkeypatch)
    config_path = _candidate_config(tmp_path)
    summary = _write_live_probe_summary(tmp_path, config_path.name, CANDIDATE_MODEL)
    result = harness.run_config(
        config_path,
        output_root=tmp_path,
        timeout_seconds=1,
        live_probe_summary=summary,
    )
    assert result == 0
    artifact = json.loads(next(tmp_path.rglob("run.json")).read_text(encoding="utf-8"))
    assert artifact["runtime_config"] == "runtime_candidate.yaml"
    assert artifact["dialogue_enable_thinking"] is False
    assert artifact["generation_options"]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_ab_08_repeatability_and_performance_are_recorded(tmp_path, monkeypatch):
    clients = _fake_factory(monkeypatch)
    config_path = _baseline_config(tmp_path)
    summary = _write_live_probe_summary(tmp_path, config_path.name, BASELINE_MODEL)
    assert harness.run_config(
        config_path, output_root=tmp_path, timeout_seconds=1, live_probe_summary=summary
    ) == 0
    client = next(iter(clients.values()))
    assert len(client.calls) == len(harness.SCENARIOS) * 2 + len(
        harness.REPEATABILITY_SCENARIO_IDS
    ) * harness.REPEAT_COUNT
    artifact = json.loads(next(tmp_path.rglob("run.json")).read_text(encoding="utf-8"))
    assert len(artifact["repeatability_results"]) == len(harness.REPEATABILITY_SCENARIO_IDS) * harness.REPEAT_COUNT
    assert {item["repeat_index"] for item in artifact["repeatability_results"]} == {1, 2, 3}
    assert artifact["performance_summary"]["tokens_per_second"] == "NOT AVAILABLE"
    assert artifact["performance_summary"]["request_failures_count"] == 0


def test_ab_09_output_leak_fails_without_stripping(tmp_path, monkeypatch):
    _fake_factory(monkeypatch)
    config_path = _baseline_config(tmp_path)
    summary = _write_live_probe_summary(tmp_path, config_path.name, BASELINE_MODEL)

    class LeakingClient(FakeDialogueClient):
        def complete_messages(self, *, messages, max_tokens):
            return "[END_QUIT]"

        def stream_messages(self, *, messages):
            yield "<think>hidden</think>"

    monkeypatch.setattr(
        harness,
        "build_dialogue_client",
        lambda config=None, *, timeout=None: LeakingClient(config),
    )
    result = harness.run_config(
        config_path, output_root=tmp_path / "run", timeout_seconds=1, live_probe_summary=summary
    )
    assert result == 1
    artifact = json.loads(next((tmp_path / "run").rglob("run.json")).read_text(encoding="utf-8"))
    assert artifact["status"] == "FAIL"
    assert artifact["leakage_failures"]
    assert "END" in artifact["leakage_failures"][0] or "think" in artifact["leakage_failures"][0]


def test_ab_10_compare_requires_all_input_parity_and_never_promotes(tmp_path, monkeypatch):
    baseline = _run_artifact(tmp_path / "runs", _baseline_config(tmp_path / "runs"), BASELINE_MODEL, monkeypatch)
    candidate = _run_artifact(tmp_path / "runs", _candidate_config(tmp_path / "runs"), CANDIDATE_MODEL, monkeypatch)
    comparison_path = tmp_path / "comparison" / "comparison.json"
    comparison = harness.compare_runs(baseline, candidate, output_path=comparison_path)
    assert comparison["status"] == "READY_FOR_HUMAN_REVIEW"
    assert comparison["promotion_status"] == "NOT APPROVED"
    assert comparison["latency_thresholds"] == "NOT DEFINED"
    assert comparison["baseline_runtime_config"] == "runtime_baseline.yaml"
    assert comparison["candidate_runtime_config"] == "runtime_candidate.yaml"
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
    assert private_map and {"runtime_config", "model", "scenario_id", "repeat_index"} <= set(private_map[0])

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


def test_ab_11_same_configuration_or_same_model_is_not_comparable(tmp_path, monkeypatch):
    baseline = _run_artifact(tmp_path / "runs", _baseline_config(tmp_path / "runs"), BASELINE_MODEL, monkeypatch)
    same_config = _run_artifact(tmp_path / "runs", _baseline_config(tmp_path / "runs"), BASELINE_MODEL, monkeypatch)
    with pytest.raises(harness.ABError, match="NOT_COMPARABLE"):
        harness.compare_runs(baseline, same_config)

    candidate = _run_artifact(tmp_path / "runs", _candidate_config(tmp_path / "runs"), CANDIDATE_MODEL, monkeypatch)
    same_model_path = tmp_path / "same_model.json"
    payload = json.loads(Path(candidate).read_text(encoding="utf-8"))
    payload.update(dialogue_model=BASELINE_MODEL)
    same_model_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(harness.ABError, match="NOT_COMPARABLE"):
        harness.compare_runs(baseline, same_model_path)


def test_ab_12_harness_has_no_service_lifecycle_or_audio_ownership():
    source = Path(harness.__file__).read_text(encoding="utf-8")
    assert "start_blackwell_stack" not in source
    assert "stop_blackwell_stack" not in source
    assert "sounddevice" not in source
    assert "VoxCPM" not in source
    assert "FunASR" not in source
    assert "DataManager" not in source
    assert "conversation_history" not in source
