"""Phase 3 ownership boundary between Pipeline and ScaleRuntime."""

from pathlib import Path

from assessment.scale_runtime import ScaleRuntime
from conversation.contracts import TurnAction, TurnDecision
from services.pipeline import ConversationPipeline, PipelineResult


ROOT = Path(__file__).resolve().parents[1]


def build_pipeline():
    return ConversationPipeline(
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        session_emotions=[],
        emotion_tracker=None,
    )


def test_pipeline_owns_one_runtime_and_turn_snapshot_reads_it():
    pipeline = build_pipeline()
    try:
        assert isinstance(pipeline.scale_runtime, ScaleRuntime)
        pipeline.scale_runtime.start("PHQ-9")

        snapshot = pipeline._build_turn_snapshot(
            round_count=6,
            time_limit_reached=False,
        )

        assert snapshot.active_scale == "PHQ-9"
        assert snapshot.current_item == 1
        assert snapshot.waiting_for_answer is True
    finally:
        pipeline.shutdown()


def test_authoritative_scale_decisions_call_runtime_commands():
    pipeline = build_pipeline()
    try:
        result = PipelineResult()
        pipeline._apply_turn_decision(
            TurnDecision(action=TurnAction.START_SCALE, scale_name="PHQ-9"),
            result,
        )
        assert pipeline.scale_runtime.snapshot().active_scale == "PHQ-9"

        pipeline._apply_turn_decision(
            TurnDecision(action=TurnAction.PAUSE_SCALE, scale_name="PHQ-9"),
            result,
        )
        assert pipeline.scale_runtime.snapshot().paused is True

        pipeline._apply_turn_decision(
            TurnDecision(action=TurnAction.CONTINUE_SCALE, scale_name="PHQ-9"),
            result,
        )
        resumed = pipeline.scale_runtime.snapshot()
        assert resumed.paused is False
        assert resumed.waiting_for_answer is True
    finally:
        pipeline.shutdown()


def test_pipeline_report_facade_returns_runtime_derived_results():
    pipeline = build_pipeline()
    try:
        pipeline.scale_runtime.start("PHQ-9")
        pipeline.scale_runtime.accept_answer(
            scale_name="PHQ-9", item=1, score=2
        )

        results = pipeline.get_scale_results()

        assert results["PHQ-9"]["answered"] == 1
        assert results["PHQ-9"]["total_score"] == 2
    finally:
        pipeline.shutdown()


def test_pipeline_has_no_legacy_scale_state_container_or_delegate_properties():
    source = (ROOT / "services" / "pipeline.py").read_text(encoding="utf-8")

    forbidden = (
        "from core.scale_fsm import ScaleState",
        "delegate_property(",
        "self._scale_state",
        "_scale_answers =",
        "_active_scale = delegate_property",
    )
    assert all(marker not in source for marker in forbidden)
