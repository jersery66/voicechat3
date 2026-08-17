"""Deterministic contracts for passive timing and performance measurement."""

from __future__ import annotations

from scripts.deployment.measurement import (
    EVIDENCE_MEASURED,
    EVIDENCE_NOT_AVAILABLE,
    EVIDENCE_SIMULATED,
    STATUS_CANCELLED,
    STATUS_SUCCESS,
    TimingRecorder,
    aggregate_metric,
    build_measurement_event,
    percentile,
    token_metrics,
    initialise_measurement_artifacts,
    build_performance_summary,
)


class FakeClock:
    def __init__(self):
        self.value = 0

    def __call__(self):
        return self.value

    def advance_ms(self, amount):
        self.value += int(amount * 1_000_000)


def test_monotonic_timing_distinguishes_first_token_and_first_sentence():
    clock = FakeClock()
    recorder = TimingRecorder(clock_ns=clock)
    recorder.mark("llm_request_start")
    clock.advance_ms(40)
    recorder.mark("llm_first_token")
    clock.advance_ms(60)
    recorder.mark("llm_first_sentence")
    clock.advance_ms(140)
    recorder.mark("llm_generation_end")

    metrics = recorder.metrics()
    assert metrics["ttft_ms"] == 40.0
    assert metrics["first_sentence_latency_ms"] == 100.0
    assert metrics["generation_latency_ms"] == 240.0
    assert recorder.status == STATUS_SUCCESS


def test_e2e_timing_contract_derives_speech_and_audio_boundaries():
    clock = FakeClock()
    recorder = TimingRecorder(clock_ns=clock)
    for event, advance in (
        ("turn_start", 0),
        ("speech_start", 10),
        ("speech_end", 500),
        ("asr_final_text", 80),
        ("llm_request_start", 20),
        ("llm_first_token", 40),
        ("llm_first_sentence", 60),
        ("tts_first_audio_ready", 100),
        ("playback_end", 200),
    ):
        clock.advance_ms(advance)
        recorder.mark(event)

    metrics = recorder.metrics()
    assert metrics["speech_duration_ms"] == 500.0
    assert metrics["speech_to_first_text_ms"] == 80.0
    assert metrics["speech_to_first_token_ms"] == 140.0
    assert metrics["speech_to_first_sentence_ms"] == 200.0
    assert metrics["speech_to_first_audio_ms"] == 300.0
    assert metrics["full_turn_duration_ms"] == 1010.0


def test_first_sentence_ready_can_be_delivery_boundary():
    clock = FakeClock()
    recorder = TimingRecorder(clock_ns=clock)
    recorder.mark("llm_request_start")
    clock.advance_ms(10)
    recorder.mark("llm_first_token")
    clock.advance_ms(20)
    recorder.mark("first_sentence_ready")
    assert recorder.metrics()["first_sentence_latency_ms"] == 30.0


def test_cancelled_generation_is_marked_and_not_successful():
    clock = FakeClock()
    recorder = TimingRecorder(clock_ns=clock)
    recorder.mark("llm_request_start")
    clock.advance_ms(10)
    recorder.cancel()
    assert recorder.status == "CANCELLED"
    assert "generation_cancelled" in recorder.event_times_ns
    events = recorder.measurement_events(component="dialogue", evidence_type=EVIDENCE_MEASURED)
    assert events
    assert all(event["status"] == "CANCELLED" for event in events)


def test_cancelled_and_failed_samples_are_not_aggregated():
    samples = [
        {"metric_name": "ttft_ms", "value": 100, "status": STATUS_SUCCESS, "evidence_type": EVIDENCE_MEASURED},
        {"metric_name": "ttft_ms", "value": 200, "status": STATUS_CANCELLED, "evidence_type": EVIDENCE_MEASURED},
        {"metric_name": "ttft_ms", "value": 300, "status": "FAILED", "evidence_type": EVIDENCE_MEASURED},
        {"metric_name": "ttft_ms", "value": 400, "status": STATUS_SUCCESS, "evidence_type": EVIDENCE_SIMULATED},
    ]
    assert aggregate_metric(samples, "ttft_ms", evidence_type=EVIDENCE_MEASURED) == {
        "count": 1,
        "median": 100.0,
        "p95": 100.0,
        "min": 100.0,
        "max": 100.0,
    }


def test_token_usage_is_explicitly_unavailable_without_real_usage():
    unavailable = token_metrics(None, generation_ms=100)
    assert unavailable["completion_tokens"] is None
    assert unavailable["tokens_per_second"] is None
    assert unavailable["status"] == "NOT AVAILABLE"

    measured = token_metrics({"prompt_tokens": 5, "completion_tokens": 20, "total_tokens": 25}, generation_ms=2000)
    assert measured["completion_tokens"] == 20
    assert measured["tokens_per_second"] == 10.0
    assert measured["status"] == STATUS_SUCCESS


def test_percentile_rule_is_deterministic():
    assert percentile([], 0.95) is None
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([1, 2, 3, 4], 0.95) == 3.85


def test_measurement_event_contains_provenance_and_nullable_identity():
    event = build_measurement_event(
        component="dialogue",
        metric_name="ttft_ms",
        value=12.5,
        unit="ms",
        evidence_type=EVIDENCE_SIMULATED,
        profile="rtxpro6000_96g",
        git_commit="test-commit",
    )
    assert event["schema_version"] == 1
    assert event["evidence_type"] == EVIDENCE_SIMULATED
    assert event["source"] == "passive_measurement"
    assert event["session_id"] is None
    assert event["generation_id"] is None
    assert event["git_commit"] == "test-commit"


def test_not_available_evidence_is_not_measured():
    event = build_measurement_event(
        component="dialogue",
        metric_name="ttft_ms",
        value=None,
        unit="ms",
        evidence_type=EVIDENCE_NOT_AVAILABLE,
        status="NOT AVAILABLE",
    )
    assert event["evidence_type"] == EVIDENCE_NOT_AVAILABLE
    assert event["value"] is None


def test_measurement_artifact_initializer_is_not_run(tmp_path):
    paths = initialise_measurement_artifacts(tmp_path, profile="rtxpro6000_96g", git_commit="test-commit")
    assert paths["performance_summary"].exists()
    assert "NOT RUN" in paths["performance_summary"].read_text(encoding="utf-8")


def test_performance_summary_never_mixes_simulated_with_measured():
    summary = build_performance_summary(
        [
            {"metric_name": "ttft_ms", "value": 100, "status": STATUS_SUCCESS, "evidence_type": EVIDENCE_MEASURED},
            {"metric_name": "ttft_ms", "value": 10, "status": STATUS_SUCCESS, "evidence_type": EVIDENCE_SIMULATED},
        ],
        profile="rtxpro6000_96g",
        git_commit="test-commit",
    )
    assert summary["status"] == EVIDENCE_MEASURED
    assert summary["metrics"]["ttft_ms"]["count"] == 1
    assert summary["metrics"]["ttft_ms"]["median"] == 100.0
    assert summary["model_comparison"] == "NOT PERFORMED"


def test_measured_data_does_not_imply_performance_acceptance():
    summary = build_performance_summary(
        [{"metric_name": "ttft_ms", "value": 1, "status": STATUS_SUCCESS, "evidence_type": EVIDENCE_MEASURED}],
        profile="rtxpro6000_96g",
    )
    assert summary["status"] == "MEASURED"
    assert summary["status"] not in {"PASS", "ACCEPTED", "PROMOTED", "WINNER"}
    assert summary["model_comparison"] == "NOT PERFORMED"
    assert summary["promotion"] == "NOT APPROVED"
