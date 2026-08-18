"""Pre-hardware integrity contracts for privacy, evidence, storage, and provenance."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from conversation.coordinator import ConversationCoordinator
from conversation.contracts import RouterAction
from data.data_manager import DataManager
from data.treatment_progress import TreatmentProgress
from deployment.profiles import get_deployment_profile
from research.event_journal import EventJournal
from research.runtime_manifest import build_runtime_manifest
from research.turn_trace import TurnTraceRecorder
from services.pipeline import PipelineConfig
from services.rag_service import RAGService
from services.report_service import EndType, ReportService
from services.stats_service import StatsService
from tests.e2e.fixtures import ScenarioHarness, proposal


def test_rag_warning_never_contains_sensitive_query(caplog):
    secret = "PRIVATE_TEST_7F92_RAG_TEXT"
    harness = ScenarioHarness()
    try:
        caplog.set_level("DEBUG")
        harness.pipeline._build_system_suffix(secret, needs_rag=True)
    finally:
        harness.shutdown()
    assert secret not in caplog.text
    assert "query_hash" in caplog.text or "RagDebug" not in caplog.text


def test_rag_service_debug_log_never_contains_sensitive_query(tmp_path, caplog):
    secret = "PRIVATE_TEST_DIRECT_RAG_8A31"
    (tmp_path / "knowledge.json").write_text(
        json.dumps([{"keywords": ["PRIVATE_TEST_DIRECT"], "title": "测试", "content": "内容"}], ensure_ascii=False),
        encoding="utf-8",
    )
    caplog.set_level("DEBUG")
    RAGService(knowledge_base_path=str(tmp_path)).get_context(secret, enabled=True)
    assert secret not in caplog.text


def test_report_records_relaxation_completion_without_claiming_relief():
    prompts = []

    class Agent:
        def generate_report(self, prompt, timeout=None):
            prompts.append(prompt)
            return '{"summary": "完成记录"}'

    service = ReportService(agent_service=Agent(), llm_service=Agent())
    service.start_session()
    report = service.generate_researcher_report(
        [{"role": "user", "content": "我做完了练习"}],
        "P1",
        EndType.QUIT,
        relaxation_info="呼吸放松训练",
    )
    assert report["summary"] == "完成记录"
    assert "已完成呼吸放松训练" in prompts[0]
    assert "有所缓解" not in prompts[0]


def test_report_failure_does_not_duplicate_raw_conversation():
    class Failing:
        def generate_report(self, *args, **kwargs):
            raise RuntimeError("synthetic report failure")

        def chat_sync(self, *args, **kwargs):
            raise RuntimeError("synthetic fallback failure")

    service = ReportService(agent_service=Failing(), llm_service=Failing())
    service.start_session()
    report = service.generate_researcher_report(
        [{"role": "user", "content": "PRIVATE_REPORT_TEXT"}],
        "P2",
        EndType.QUIT,
    )
    assert report["report_generation_failed"] is True
    assert "raw_conversation" not in report
    assert "PRIVATE_REPORT_TEXT" not in json.dumps(report, ensure_ascii=False)


def test_stats_are_descriptive_scale_changes_not_treatment_effect():
    root = Path(".") / "test_output" / "integrity-stats-fixture"
    summaries = root / "session_summaries"
    summaries.mkdir(parents=True, exist_ok=True)
    path = summaries / "P3_progress.json"
    path.write_text(json.dumps({
        "schema_version": 2,
        "subject_id": "P3",
        "sessions": [
            {"date": "2026-01-01", "duration_minutes": 1, "end_type": "QUIT", "key_events": [],
             "scale_scores": {"PHQ-9": {"total": 18}}},
            {"date": "2026-01-02", "duration_minutes": 1, "end_type": "QUIT", "key_events": [],
             "scale_scores": {"PHQ-9": {"total": 17}}},
        ],
        "scale_trend": {"PHQ-9": [{"date": "2026-01-01", "total": 18}, {"date": "2026-01-02", "total": 17}]},
    }, ensure_ascii=False), encoding="utf-8")
    try:
        stats = StatsService(str(root)).get_group_stats()
        assert "improvement_rate" not in stats
        assert stats["scale_changes"]["PHQ-9"]["paired_n"] == 1
        assert stats["scale_changes"]["PHQ-9"]["mean_delta"] == -1.0
    finally:
        path.unlink(missing_ok=True)
        summaries.rmdir()
        root.rmdir()


def test_model_emotion_observations_are_not_reinjected_as_progress_conclusions(tmp_path):
    progress = TreatmentProgress(str(tmp_path))
    progress.add_session("P4", "2026-01-01", "s1", {"dominant_emotion": "anxious", "avg_intensity": 0.8, "trend": "worsening"}, {}, [], 1, 1, "QUIT")
    progress.add_session("P4", "2026-01-02", "s2", {"dominant_emotion": "neutral", "avg_intensity": 0.3, "trend": "improving"}, {}, [], 1, 1, "QUIT")
    summary = progress.get_progress_summary("P4")
    assert "好转" not in summary
    assert "情绪趋势" not in summary
    assert "MODEL_EMOTION_OBSERVATION" not in summary


def test_data_manager_writes_json_and_text_atomically(tmp_path, monkeypatch):
    dm = DataManager(str(tmp_path))
    json_path = tmp_path / "data.json"
    text_path = tmp_path / "data.txt"
    assert dm._write_json(json_path, {"schema_version": 2, "ok": True}) is True
    assert dm._write_text(text_path, "stable") is True
    assert json.loads(json_path.read_text(encoding="utf-8"))["ok"] is True
    assert text_path.read_text(encoding="utf-8") == "stable"
    monkeypatch.setattr("data.atomic_io.os.replace", lambda *_args: (_ for _ in ()).throw(OSError("interrupted")))
    assert dm._write_json(json_path, {"schema_version": 2, "ok": False}) is False
    assert json.loads(json_path.read_text(encoding="utf-8"))["ok"] is True
    assert not list(tmp_path.glob("*.tmp"))


def test_new_session_writes_runtime_manifest_without_absolute_model_paths(tmp_path):
    dm = DataManager(str(tmp_path))
    dm.set_user_id("P5")
    dm.start_new_session()
    manifest_path = Path(dm.session_dir) / "runtime_manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifact_type"] == "SESSION_RUNTIME_MANIFEST"
    assert "D:\\" not in json.dumps(manifest, ensure_ascii=False)


def test_runtime_manifest_is_reproducible_and_contains_no_secrets():
    manifest = build_runtime_manifest(
        profile=get_deployment_profile("rtxpro6000_96g"),
        git_sha="abc123",
        started_at="2026-01-01T00:00:00Z",
    )
    encoded = json.dumps(manifest, ensure_ascii=False)
    assert manifest["git_sha"] == "abc123"
    assert manifest["dialogue_model"] == "Qwen/Qwen2.5-72B-Instruct-AWQ"
    assert "api_key" not in encoded.lower()
    assert "password" not in encoded.lower()
    assert "D:\\" not in encoded


def test_runtime_manifest_uses_effective_dev_override_and_prompt_override():
    from deployment.profiles import DeploymentProfile

    profile = DeploymentProfile(
        name="test-dev",
        expected_gpu_memory_gb=6,
        runtime_backend="ollama",
        dialogue_model="default-dialogue",
        dialogue_base_url="http://localhost:11434",
        router_model="default-agent",
        agent_model="default-agent",
        agent_base_url="http://localhost:11434/v1",
        enable_streaming_tts=False,
        system_prompt_override="effective prompt override",
    )
    manifest = build_runtime_manifest(
        profile=profile,
        git_sha="dev-sha",
        environment={
            "VOICECHAT_DIALOGUE_MODEL": "override-dialogue",
            "AGENT_MODEL": "override-agent",
            "VOICECHAT_DIALOGUE_BASE_URL": "http://127.0.0.1:19000",
        },
    )
    assert manifest["dialogue_model"] == "override-dialogue"
    assert manifest["agent_model"] == "override-agent"
    assert manifest["dialogue_base_url"] == "http://127.0.0.1:19000"
    assert manifest["effective_prompt_hash"] == manifest["prompt_version"]
    assert manifest["git_dirty"] in (True, False, None)


def test_runtime_manifest_keeps_immutable_profile_frozen_despite_environment():
    manifest = build_runtime_manifest(
        profile=get_deployment_profile("rtxpro6000_96g"),
        git_sha="prod-sha",
        environment={"VOICECHAT_DIALOGUE_MODEL": "wrong-model", "AGENT_MODEL": "wrong-agent"},
    )
    assert manifest["dialogue_model"] == "Qwen/Qwen2.5-72B-Instruct-AWQ"
    assert manifest["agent_model"] == "Qwen/Qwen2.5-3B-Instruct-AWQ"


def test_turn_trace_is_deidentified_and_stage_shaped(tmp_path):
    recorder = TurnTraceRecorder(tmp_path / "turn_trace.jsonl", session_id="research-session")
    recorder.record(
        turn_id=1,
        input_mode="text",
        user_text="PRIVATE_TRACE_TEXT",
        turn_action=RouterAction.CHAT.value,
        session_state="CHATTING",
        rag_used=False,
    )
    encoded = (tmp_path / "turn_trace.jsonl").read_text(encoding="utf-8")
    assert "PRIVATE_TRACE_TEXT" not in encoded
    record = json.loads(encoded)
    assert record["session_id"] == "research-session"
    assert record["turn_id"] == 1
    assert "dialogue_ttft_ms" in record
    assert "turn_action" in record


def test_coordinator_can_write_deidentified_turn_trace(tmp_path):
    from tests.test_conversation_coordinator import FakePipeline

    trace = TurnTraceRecorder(tmp_path / "trace.jsonl", session_id="r1")
    coordinator = ConversationCoordinator(
        pipeline=FakePipeline(),
        journal=EventJournal(tmp_path / "events.jsonl"),
        trace_recorder=trace,
    )
    coordinator.execute(PipelineConfig(user_text="PRIVATE_COORDINATOR_TEXT"), lambda *_: None)
    encoded = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert "PRIVATE_COORDINATOR_TEXT" not in encoded


def test_production_style_main_window_trace_wiring_rotates_per_session(tmp_path):
    from tests.test_conversation_coordinator import FakePipeline
    from ui.main_window import MainWindow

    window = MainWindow.__new__(MainWindow)
    window.data_manager = SimpleNamespace(session_dir=str(tmp_path / "session_a"))
    coordinator = ConversationCoordinator(pipeline=FakePipeline())
    coordinator.start_research_session()
    first_session_id = coordinator.session_id
    window.conversation_coordinator = coordinator
    window._attach_session_trace()
    coordinator.execute(PipelineConfig(user_text="PRIVATE_TRACE_A"), lambda *_: None)

    window.data_manager.session_dir = str(tmp_path / "session_b")
    coordinator.start_research_session()
    second_session_id = coordinator.session_id
    window._attach_session_trace()
    coordinator.execute(PipelineConfig(user_text="PRIVATE_TRACE_B"), lambda *_: None)

    trace_a = (tmp_path / "session_a" / "turn_trace.jsonl").read_text(encoding="utf-8")
    trace_b = (tmp_path / "session_b" / "turn_trace.jsonl").read_text(encoding="utf-8")
    assert first_session_id != second_session_id
    assert "PRIVATE_TRACE_A" not in trace_a
    assert "PRIVATE_TRACE_B" not in trace_b
    assert first_session_id in trace_a
    assert second_session_id in trace_b
    assert "turn_action" in trace_a and "turn_action" in trace_b


def test_trace_keeps_measured_timings_and_nullable_unmeasured_fields(tmp_path):
    from tests.test_conversation_coordinator import FakePipeline
    class TimedPipeline(FakePipeline):
        def execute(self, config, emit):
            result = super().execute(config, emit)
            result.timing = {"agent_ms": 2.5, "rag_ms": 4.0}
            return result

    trace = TurnTraceRecorder(tmp_path / "timed.jsonl", session_id="r2")
    coordinator = ConversationCoordinator(pipeline=TimedPipeline(), trace_recorder=trace)
    coordinator.execute(PipelineConfig(user_text="synthetic"), lambda *_: None)
    record = json.loads((tmp_path / "timed.jsonl").read_text(encoding="utf-8"))
    assert record["agent_ms"] == 2.5
    assert record["rag_ms"] == 4.0
    assert record["asr_ms"] is None
    assert record["vad_end_ms"] is None
    assert record["tts_first_audio_ms"] is None


def test_real_pipeline_timing_fields_are_forwarded_when_measurable(tmp_path):
    harness = ScenarioHarness(responses=["这是一句可交付的测试回应。"])
    trace = TurnTraceRecorder(tmp_path / "pipeline-trace.jsonl", session_id="r3")
    harness.pipeline  # keep the real pipeline boundary explicit for this test
    coordinator = ConversationCoordinator(
        pipeline=harness.pipeline,
        trace_recorder=trace,
    )
    coordinator.execute(
        PipelineConfig(
            user_text="测试 RAG 追踪",
            router_proposal=proposal(RouterAction.CHAT, needs_rag=True),
            generation_id=harness.new_generation(),
        ),
        harness.emit,
    )
    record = json.loads((tmp_path / "pipeline-trace.jsonl").read_text(encoding="utf-8"))
    assert record["turn_policy_ms"] is not None
    assert record["rag_ms"] is not None
    assert record["dialogue_ttft_ms"] is not None
    assert record["first_sentence_ms"] is not None
    assert record["asr_ms"] is None
    harness.shutdown()
