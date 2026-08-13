"""Vertical-slice tests for the new conversation coordinator."""

import json

from conversation.contracts import PolicyDecision, ScaleAction
from conversation.coordinator import ConversationCoordinator
from research.event_journal import EventJournal
from services.pipeline import PipelineConfig, PipelineResult


class FakePipeline:
    def __init__(self):
        self.calls = []

    def execute(self, config, emit):
        self.calls.append(config)
        emit("append_chat", ("user", config.user_text))
        return PipelineResult(user_text=config.user_text, full_response="ok")


class VoiceCapableFakePipeline(FakePipeline):
    def __init__(self, transcript):
        super().__init__()
        self.transcript = transcript
        self.transcribe_calls = []

    def transcribe(self, audio_data, emit):
        self.transcribe_calls.append(audio_data)
        return self.transcript


def test_legacy_agent_route_becomes_a_typed_policy_decision():
    decision = PolicyDecision.from_agent_route(
        {"scale_action": "start", "scale": "phq9", "item": 2, "confidence": 0.91}
    )

    assert decision.scale_action == ScaleAction.START
    assert decision.scale_name == "PHQ-9"
    assert decision.scale_item == 2


def test_normal_turn_flows_through_legacy_pipeline_and_is_journaled(tmp_path):
    pipeline = FakePipeline()
    events = []
    journal_path = tmp_path / "events.jsonl"
    coordinator = ConversationCoordinator(
        pipeline=pipeline,
        journal=EventJournal(journal_path),
    )

    result = coordinator.execute(
        PipelineConfig(user_text="normal input"), lambda *event: events.append(event)
    )

    assert result.full_response == "ok"
    assert len(pipeline.calls) == 1
    records = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    assert [record["type"] for record in records] == [
        "policy_decision", "turn_completed"
    ]
    assert all("user_text" not in record["payload"] for record in records)


def test_router_reason_is_not_written_to_the_research_journal(tmp_path):
    class RoutedPipeline(FakePipeline):
        def execute(self, config, emit):
            result = super().execute(config, emit)
            result.agent_route = {"scale_action": "none", "reason": "echoed private text"}
            return result

    journal_path = tmp_path / "events.jsonl"
    coordinator = ConversationCoordinator(pipeline=RoutedPipeline(), journal=EventJournal(journal_path))

    coordinator.execute(PipelineConfig(user_text="normal input"), lambda *_event: None)

    policy = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()
              if json.loads(line)["type"] == "policy_decision"][0]
    assert policy["payload"]["reason"] == ""


def test_crisis_keyword_text_still_flows_through_legacy_pipeline(tmp_path):
    pipeline = FakePipeline()
    events = []
    coordinator = ConversationCoordinator(
        pipeline=pipeline,
        journal=EventJournal(tmp_path / "events.jsonl"),
    )

    result = coordinator.execute(
        PipelineConfig(user_text="我准备今晚割腕"),
        lambda *event: events.append(event),
    )

    assert len(pipeline.calls) == 1
    assert result.full_response == "ok"
    assert all(event[0] != "show_crisis" for event in events)


def test_crisis_keyword_voice_transcript_is_transcribed_once_then_runs_pipeline(tmp_path):
    pipeline = VoiceCapableFakePipeline("我准备今晚割腕")
    events = []
    coordinator = ConversationCoordinator(pipeline=pipeline, journal=EventJournal(tmp_path / "events.jsonl"))

    result = coordinator.execute(
        PipelineConfig(use_stt=True, audio_data=[1]), lambda *event: events.append(event)
    )

    assert pipeline.transcribe_calls == [[1]]
    assert len(pipeline.calls) == 1
    assert pipeline.calls[0].transcribed_text == "我准备今晚割腕"
    assert result.full_response == "ok"
    assert all(event[0] != "show_crisis" for event in events)


def test_blank_voice_transcript_does_not_enter_pipeline_and_is_journaled(tmp_path):
    pipeline = VoiceCapableFakePipeline("   \t")
    journal_path = tmp_path / "events.jsonl"
    coordinator = ConversationCoordinator(
        pipeline=pipeline,
        journal=EventJournal(journal_path),
        session_id="research-session",
    )

    result = coordinator.execute(PipelineConfig(use_stt=True, audio_data=[1]), lambda *_event: None)

    assert result == PipelineResult()
    assert pipeline.transcribe_calls == [[1]]
    assert pipeline.calls == []
    records = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    assert [record["type"] for record in records] == ["turn_completed"]
    assert records[0]["session_id"] == "research-session"


def test_voice_transcript_preserves_original_pipeline_config_fields(tmp_path):
    pipeline = VoiceCapableFakePipeline("transcribed")
    coordinator = ConversationCoordinator(pipeline=pipeline)
    config = PipelineConfig(
        use_stt=True,
        use_tts=True,
        audio_data=[1],
        user_text="caller-provided fallback",
        extra_system_suffix="scale context",
    )

    coordinator.execute(config, lambda *_event: None)

    assert len(pipeline.calls) == 1
    forwarded = pipeline.calls[0]
    assert forwarded.user_text == "caller-provided fallback"
    assert forwarded.use_tts is True
    assert forwarded.extra_system_suffix == "scale context"
    assert forwarded.transcribed_text == "transcribed"


def test_safe_voice_transcript_is_transcribed_once_then_runs_legacy_pipeline(tmp_path):
    pipeline = VoiceCapableFakePipeline("我今天有点累")
    journal = EventJournal(tmp_path / "events.jsonl")
    coordinator = ConversationCoordinator(pipeline=pipeline, journal=journal)

    result = coordinator.execute(PipelineConfig(use_stt=True, audio_data=[1]), lambda *_event: None)

    assert pipeline.transcribe_calls == [[1]]
    assert len(pipeline.calls) == 1
    assert pipeline.calls[0].use_stt is True
    assert pipeline.calls[0].transcribed_text == "我今天有点累"
    assert result.full_response == "ok"
    records = [json.loads(line) for line in journal.path.read_text(encoding="utf-8").splitlines()]
    assert [record["type"] for record in records] == [
        "policy_decision", "turn_completed"
    ]
    assert records[-1]["payload"] == {"input_mode": "voice", "end_type": None}


def test_decide_turn_returns_typed_policy_without_safety_journal(tmp_path):
    journal_path = tmp_path / "events.jsonl"
    coordinator = ConversationCoordinator(
        pipeline=FakePipeline(),
        journal=EventJournal(journal_path),
    )

    decision = coordinator.decide_turn(
        {"scale_action": "start", "scale": "phq9", "item": 2, "confidence": 0.91}
    )

    assert isinstance(decision, PolicyDecision)
    assert decision.scale_action == ScaleAction.START
    records = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    assert [record["type"] for record in records] == ["policy_decision"]
