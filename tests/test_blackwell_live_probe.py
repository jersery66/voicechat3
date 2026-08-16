"""Deterministic tests for the real Blackwell acceptance probe.

These tests exercise command construction, profile ownership, response
inspection, timing, and artifact failure semantics without contacting WSL,
GPU drivers, vLLM, or external services.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from deployment.profiles import get_deployment_profile, resolve_runtime_models
from scripts.acceptance import blackwell_live_probe as probe
from scripts.acceptance import probe_support as support


def test_probe_01_only_blackwell_profiles_are_supported():
    assert support.SUPPORTED_PROFILES == {
        "rtxpro6000_96g",
        "rtxpro6000_96g_qwen38_candidate",
    }
    for name in support.SUPPORTED_PROFILES:
        profile, models = support.validate_profile(name)
        assert profile.name == name
        assert profile.expected_gpu_memory_gb == 96
        assert models.dialogue == profile.dialogue_model

    for name in ("dev_6g", "dev_vllm_6g", "a100_80g", "a100_80g_qwen38_candidate"):
        with pytest.raises(support.ProbeError):
            support.validate_profile(name)


def test_probe_02_profile_is_explicit_and_not_hardware_selected():
    signature = inspect.signature(probe.run_probe)
    assert "profile_name" in signature.parameters
    source = Path(probe.__file__).read_text(encoding="utf-8")
    support_source = Path(support.__file__).read_text(encoding="utf-8")
    assert "validate_profile(profile_name)" in source
    assert "get_deployment_profile(name)" in support_source
    assert "nvidia-smi" in support_source
    assert "get_deployment_profile(gpu" not in source


def test_probe_03_windows_nvidia_smi_query_is_structured(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout="0, NVIDIA RTX PRO 6000 Blackwell Workstation Edition, 98304, 100, 98204, 555.1, GPU-1\n",
            stderr="",
        )

    monkeypatch.setattr(support, "run_command", fake_run)
    observed = support.query_windows_gpu(timeout_seconds=3)
    assert calls[0][0] == "nvidia-smi"
    assert "--query-gpu=index,name,memory.total,memory.used,memory.free,driver_version,uuid" in calls[0]
    assert observed[0].name.startswith("NVIDIA RTX PRO 6000")


def test_probe_04_wsl_nvidia_smi_query_uses_selected_distro(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout="0, NVIDIA RTX PRO 6000 Blackwell, 98304, 200, 98104, 555.1, GPU-1\n",
            stderr="",
        )

    monkeypatch.setattr(support, "run_command", fake_run)
    support.query_wsl_gpu("Ubuntu-24.04", timeout_seconds=3)
    assert calls[0][:3] == ["wsl.exe", "-d", "Ubuntu-24.04"]
    assert "nvidia-smi" in calls[0]


def test_probe_05_single_gpu_contract_rejects_multiple_devices():
    observations = [
        support.GPUObservation(0, "NVIDIA RTX PRO 6000 Blackwell", 98304, 0, 98304, "d", "u0"),
        support.GPUObservation(1, "NVIDIA RTX PRO 6000 Blackwell", 98304, 0, 98304, "d", "u1"),
    ]
    with pytest.raises(support.ProbeError, match="UNSUPPORTED_MULTI_GPU_CURRENT_CONTRACT"):
        support.validate_gpu_observation(observations, source="windows")


def test_probe_06_wrong_gpu_family_fails():
    observation = support.GPUObservation(0, "NVIDIA A100 80GB", 81920, 0, 81920, "d", "u")
    with pytest.raises(support.ProbeError, match="HARDWARE_PROFILE_MISMATCH"):
        support.validate_gpu_observation([observation], source="windows")


def test_probe_07_insufficient_memory_fails():
    observation = support.GPUObservation(0, "NVIDIA RTX PRO 6000 Blackwell", 81920, 0, 81920, "d", "u")
    with pytest.raises(support.ProbeError, match="HARDWARE_PROFILE_MISMATCH"):
        support.validate_gpu_observation([observation], source="windows")


def test_probe_08_windows_wsl_gpu_consistency_is_checked():
    windows = support.GPUObservation(0, "NVIDIA RTX PRO 6000 Blackwell", 98304, 0, 98304, "d", "u")
    wsl = support.GPUObservation(0, "NVIDIA RTX PRO 6000 Blackwell", 65536, 0, 65536, "d", "u")
    with pytest.raises(support.ProbeError, match="GPU_CONSISTENCY_MISMATCH"):
        support.compare_gpu_observations(windows, wsl)


def test_probe_09_exact_dialogue_model_identity_is_required():
    assert support.require_exact_model(["expected"], "expected", "dialogue") == "expected"
    with pytest.raises(support.ProbeError, match="MODEL_IDENTITY_MISMATCH"):
        support.require_exact_model(["other"], "expected", "dialogue")


def test_probe_10_exact_agent_model_identity_is_required():
    with pytest.raises(support.ProbeError, match="MODEL_IDENTITY_MISMATCH"):
        support.require_exact_model([], "agent-model", "agent")


def test_probe_11_agent_probe_uses_direct_json_request():
    calls = []

    def call_json(**kwargs):
        calls.append(kwargs)
        return {"status": "ok"}

    result = probe.run_agent_json_probe(call_json)
    assert result["status"] == "PASS"
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["messages"][1]["content"]


@pytest.mark.parametrize("payload", [{}, {"status": "wrong"}])
def test_probe_11b_agent_probe_requires_ok_status(payload):
    with pytest.raises(support.ProbeError, match="AGENT_SEMANTIC_MISMATCH"):
        probe.run_agent_json_probe(lambda **kwargs: payload)


def test_probe_11c_agent_probe_accepts_exact_ok_status():
    result = probe.run_agent_json_probe(lambda **kwargs: {"status": "ok"})
    assert result["status"] == "PASS"


def test_probe_12_invalid_agent_json_fails():
    def call_json(**kwargs):
        return "not-json"

    with pytest.raises(support.ProbeError, match="AGENT_INVALID_JSON"):
        probe.run_agent_json_probe(call_json)


def test_probe_13_dialogue_client_uses_profile_factory(monkeypatch):
    profile = get_deployment_profile("rtxpro6000_96g")
    models = resolve_runtime_models(profile, environment={})
    calls = []

    class FakeClient:
        model = profile.dialogue_model
        dialogue_enable_thinking = None

    def fake_factory(received_profile, received_models, *, timeout):
        calls.append((received_profile, received_models, timeout))
        return FakeClient()

    monkeypatch.setattr(probe, "build_dialogue_client", fake_factory)
    client = probe.build_profile_dialogue_client(profile, models, timeout_seconds=7)
    assert isinstance(client, FakeClient)
    assert calls == [(profile, models, 7.0)]


def test_probe_14_nonstream_empty_response_fails():
    class EmptyClient:
        def complete_messages(self, **kwargs):
            return "   "

    with pytest.raises(support.ProbeError, match="DIALOGUE_NONSTREAM_EMPTY"):
        probe.run_dialogue_nonstream_probe(EmptyClient(), timeout_seconds=2)


def test_probe_15_streaming_requires_participant_content():
    class EmptyClient:
        def stream_messages(self, **kwargs):
            return iter(["", " "])

    with pytest.raises(support.ProbeError, match="DIALOGUE_STREAM_EMPTY"):
        probe.run_dialogue_stream_probe(EmptyClient(), timeout_seconds=2, clock=iter([1.0, 2.0, 3.0]).__next__)


def test_probe_16_streaming_ttft_is_measured_from_first_content():
    clock_values = iter([10.0, 10.1, 10.4]).__next__

    class Client:
        def stream_messages(self, **kwargs):
            return iter(["", "第一句", "第二句"])

    result = probe.run_dialogue_stream_probe(Client(), timeout_seconds=2, clock=clock_values)
    assert result["status"] == "PASS"
    assert result["first_content"] == "第一句"
    assert result["client_ttft_ms"] == pytest.approx(100.0)
    assert result["content"] == "第一句第二句"


def test_probe_17_qwen38_candidate_requires_non_thinking():
    profile = get_deployment_profile("rtxpro6000_96g_qwen38_candidate")

    class Client:
        dialogue_enable_thinking = True

        def _generation_options(self):
            return {"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}}

    with pytest.raises(support.ProbeError, match="THINKING_CONFIG_MISSING"):
        probe.validate_thinking_contract(profile, Client())


def test_probe_17b_thinking_contract_is_profile_owned_after_profile_rename():
    profile = replace(
        get_deployment_profile("rtxpro6000_96g_qwen38_candidate"),
        name="future_blackwell_dialogue_candidate",
    )

    class Client:
        dialogue_enable_thinking = False

        def _generation_options(self):
            return {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}

    probe.validate_thinking_contract(profile, Client())


def test_probe_18_qwen25_baseline_does_not_receive_thinking_kwargs():
    profile = get_deployment_profile("rtxpro6000_96g")

    class Client:
        dialogue_enable_thinking = None

        def _generation_options(self):
            return {"temperature": 0.35}

    probe.validate_thinking_contract(profile, Client())


def test_probe_19_visible_think_markup_is_detected():
    report = support.inspect_leakage("hello <think>secret</think>")
    assert report["thinking_markup"]


def test_probe_20_nonempty_reasoning_fields_are_detected_without_retaining_text():
    report = support.inspect_leakage(
        "visible",
        raw_values=[{"reasoning_content": "do not persist this"}],
    )
    assert report["reasoning_fields"] == {"reasoning_content": 19}
    assert "do not persist" not in json.dumps(report, ensure_ascii=False)


def test_probe_21_control_tag_leak_is_detected():
    report = support.inspect_leakage("你好 [END_SESSION]")
    assert report["control_tags"] == ["[END_"]


def test_probe_22_absent_server_metrics_are_not_failure():
    result = support.extract_server_metrics({})
    assert result == "UNAVAILABLE / NOT ENABLED"


def test_probe_23_server_metrics_are_recorded_when_present():
    result = support.extract_server_metrics({"time_to_first_token_ms": 42.5, "tokens_per_second": 9.0})
    assert result["time_to_first_token_ms"] == 42.5
    assert result["tokens_per_second"] == 9.0


def test_probe_24_gpu_snapshots_are_json_serializable():
    observation = support.GPUObservation(0, "NVIDIA RTX PRO 6000 Blackwell", 98304, 100, 98204, "d", "u", 12.0)
    assert observation.to_dict()["memory_used_mib"] == 100
    assert observation.to_dict()["utilization_gpu"] == 12.0


def test_probe_25_pass_status_returns_zero():
    assert probe.exit_code_for_status("PASS") == 0


def test_probe_26_fail_status_returns_nonzero():
    assert probe.exit_code_for_status("FAIL") != 0


def test_probe_27_failure_still_writes_summary_artifact(tmp_path):
    path = probe.write_summary_artifact(tmp_path, {"overall_status": "FAIL", "errors": ["x"]})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["overall_status"] == "FAIL"


def test_probe_28_probe_source_has_no_model_lifecycle_control():
    source = Path(probe.__file__).read_text(encoding="utf-8")
    support_source = Path(support.__file__).read_text(encoding="utf-8")
    for text in (source, support_source):
        assert "start_vllm" not in text
        assert "stop_vllm" not in text
        assert "killall" not in text
        assert "pkill" not in text


def test_probe_29_probe_source_does_not_import_stt_or_tts():
    for path in (Path(probe.__file__), Path(support.__file__)):
        source = path.read_text(encoding="utf-8").lower()
        assert "stt_service" not in source
        assert "tts_service" not in source
        assert "funasr" not in source
        assert "voxcpm" not in source


def test_probe_30_probe_prompts_are_synthetic_fixed_text():
    source = Path(probe.__file__).read_text(encoding="utf-8")
    assert "connectivity probe" in source
    assert "synthetic" in source.lower()
    assert "DataManager" not in source


def _raw_chunk(content="", *, usage=None, reasoning_content=None):
    delta = SimpleNamespace(content=content)
    if reasoning_content is not None:
        delta.reasoning_content = reasoning_content
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta)],
        usage=usage,
    )


def _raw_client(chunks, calls):
    class Completions:
        def create(self, **request):
            calls.append(request)
            return iter(chunks)

    class Client:
        model = "dialogue-model"
        request_mode = "chat"

        def _prepare_chat_messages(self, messages):
            return list(messages)

        def _generation_options(self, requested_max_tokens=None):
            return {"temperature": 0.7, "max_tokens": requested_max_tokens or 64}

    client = Client()
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    return client


def test_probe_raw_stream_evidence_uses_one_request_for_timing_usage_and_content():
    calls = []
    chunks = [
        _raw_chunk("第一句"),
        _raw_chunk("第二句"),
        _raw_chunk(usage=SimpleNamespace(prompt_tokens=5, completion_tokens=4, total_tokens=9)),
    ]
    clock = iter([10.0, 10.2, 10.5]).__next__
    result = probe.run_raw_stream_acceptance(_raw_client(chunks, calls), clock=clock)
    assert len(calls) == 1
    assert result["content"] == "第一句第二句"
    assert result["completion_tokens"] == 4
    assert result["raw_stream_ttft_ms"] == pytest.approx(200.0)
    assert result["raw_stream_total_latency_ms"] == pytest.approx(500.0)
    assert result["raw_stream_output_tokens_per_second"] == pytest.approx(8.0)


def test_probe_production_stream_smoke_remains_separate_from_raw_acceptance():
    smoke_calls = []

    class SmokeClient:
        def stream_messages(self, **kwargs):
            smoke_calls.append(kwargs)
            return iter(["smoke"])

    smoke = probe.run_dialogue_stream_probe(
        SmokeClient(),
        timeout_seconds=2,
        clock=iter([1.0, 1.1, 1.2]).__next__,
    )
    raw_calls = []
    raw = probe.run_raw_stream_acceptance(
        _raw_client([_raw_chunk("raw")], raw_calls),
        clock=iter([2.0, 2.1, 2.2]).__next__,
    )
    assert smoke["content"] == "smoke"
    assert raw["content"] == "raw"
    assert len(smoke_calls) == 1
    assert len(raw_calls) == 1


def test_probe_raw_stream_reasoning_from_that_exact_stream_fails():
    chunks = [_raw_chunk("可见内容", reasoning_content="隐藏推理")]
    result = probe.run_raw_stream_acceptance(_raw_client(chunks, []), clock=iter([1.0, 1.1, 1.2]).__next__)
    with pytest.raises(support.ProbeError, match="REASONING_FIELD_LEAK"):
        probe._check_leakage_or_raise(result["content"], result["raw_chunks"])


def test_probe_raw_stream_clean_evidence_passes_leakage_check():
    result = probe.run_raw_stream_acceptance(
        _raw_client([_raw_chunk("干净回复")], []),
        clock=iter([1.0, 1.1, 1.2]).__next__,
    )
    report = probe._check_leakage_or_raise(result["content"], result["raw_chunks"])
    assert report["reasoning_fields"] == {}


def test_probe_wsl_nvidia_smi_falls_back_to_wsl_lib_path(monkeypatch):
    calls = []
    result_ok = SimpleNamespace(
        returncode=0,
        stdout="0, NVIDIA RTX PRO 6000 Blackwell, 98304, 200, 98104, 555.1, GPU-1\n",
        stderr="",
    )
    result_missing = SimpleNamespace(returncode=127, stdout="", stderr="nvidia-smi: command not found")

    def fake_run(command, **kwargs):
        calls.append(command)
        return result_missing if len(calls) == 1 else result_ok

    monkeypatch.setattr(support, "run_command", fake_run)
    observations = support.query_wsl_gpu("Ubuntu-24.04", timeout_seconds=3)
    assert observations[0].name.startswith("NVIDIA RTX PRO 6000")
    assert calls[0][4] == "nvidia-smi"
    assert calls[1][4] == "/usr/lib/wsl/lib/nvidia-smi"


def test_probe_wsl_nvidia_smi_fallback_failure_is_probe_error(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=127, stdout="", stderr="nvidia-smi: command not found")

    monkeypatch.setattr(support, "run_command", fake_run)
    with pytest.raises(support.ProbeError, match="GPU_QUERY_FAILED"):
        support.query_wsl_gpu(timeout_seconds=3)
    assert len(calls) == 2
