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
        "safety_decision", "policy_decision", "turn_completed"
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


def test_emergency_turn_bypasses_dialogue_generation_and_uses_legacy_ui_event(tmp_path):
    pipeline = FakePipeline()
    events = []
    coordinator = ConversationCoordinator(
        pipeline=pipeline,
        journal=EventJournal(tmp_path / "events.jsonl"),
    )

    result = coordinator.execute(
        PipelineConfig(user_text="\u6211\u51c6\u5907\u4eca\u665a\u5272\u8155"),
        lambda *event: events.append(event),
    )

    assert pipeline.calls == []
    # Legacy risk handling opens the crisis UI without silently auto-ending a
    # session. The new boundary preserves that behavior while bypassing LLM.
    assert result.end_type is None
    assert result.crisis_risk == 9
    assert ("show_crisis", result.safety_payload) in events


def test_voice_turn_is_kept_on_the_compatibility_path_until_streaming_asr_moves(tmp_path):
    pipeline = FakePipeline()
    journal = EventJournal(tmp_path / "events.jsonl")
    coordinator = ConversationCoordinator(pipeline=pipeline, journal=journal)

    coordinator.execute(PipelineConfig(use_stt=True), lambda *_event: None)

    assert len(pipeline.calls) == 1
    records = [json.loads(line) for line in journal.path.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["payload"] == {"input_mode": "voice", "end_type": None}
