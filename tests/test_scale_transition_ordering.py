"""Correction A3/A4: scale transition precedes language and disables RAG."""

from __future__ import annotations

from conversation.contracts import RouterAction, TurnAction
from tests.e2e.fixtures import ScenarioHarness, proposal


def test_accepted_answer_updates_runtime_before_language_context():
    harness = ScenarioHarness(start_round=5, responses=["请自然询问当前内容。", "承接并继续。"])
    try:
        first, _ = harness.run_turn(
            "最近睡不着",
            proposal(RouterAction.START_SCALE, scale="PHQ-9", needs_rag=True),
        )
        assert first.turn_decision.action is TurnAction.START_SCALE
        assert first.turn_decision.needs_rag is False
        assert harness.rag.calls == []
        second, _ = harness.run_turn("做什么都没劲，几乎每天", proposal(RouterAction.CHAT, needs_rag=True))
        assert second.turn_decision.action is TurnAction.CONTINUE_SCALE
        assert second.turn_decision.needs_rag is False
        assert harness.rag.calls == []
        assert harness.pipeline.scale_runtime.answers_by_scale["PHQ-9"][1] == 3
        assert harness.pipeline.scale_runtime.current_item == 2
        expected_question = harness.pipeline.get_active_scale_question_text()
        assert expected_question in harness.llm.calls[-1]["system_suffix"]
    finally:
        harness.shutdown()


def test_ambiguous_answer_keeps_current_item_and_does_not_retrieve_rag():
    harness = ScenarioHarness(start_round=5, responses=["问当前内容。", "请澄清当前内容。"])
    try:
        harness.run_turn("最近睡不着", proposal(RouterAction.START_SCALE, scale="PHQ-9", needs_rag=True))
        result, _ = harness.run_turn("有时候吧", proposal(RouterAction.CHAT, needs_rag=True))
        assert result.turn_decision.action is TurnAction.CONTINUE_SCALE
        assert harness.pipeline.scale_runtime.current_item == 1
        assert harness.pipeline.scale_runtime.answers_by_scale["PHQ-9"] == {}
        assert harness.rag.calls == []
    finally:
        harness.shutdown()


def test_last_item_completes_before_language_generation():
    harness = ScenarioHarness(start_round=5, responses=["开始。", "完成承接。"])
    try:
        harness.pipeline.scale_runtime.start("GAD-7")
        for item in range(1, 7):
            harness.pipeline.scale_runtime.accept_answer(item, 0)
            harness.pipeline.scale_runtime.present_current_item()
        result, _ = harness.run_turn("总觉得不好，几乎每天", proposal(RouterAction.CHAT, needs_rag=True))
        assert result.turn_decision.action is TurnAction.CONTINUE_SCALE
        assert result.scale_completed is True
        assert harness.pipeline.scale_runtime.active_scale is None
        assert "已经完成" in harness.llm.calls[-1]["system_suffix"]
    finally:
        harness.shutdown()


def test_accepted_scale_answer_survives_generation_cancellation():
    harness = ScenarioHarness(start_round=5, responses=["开始。", "承接。"])
    try:
        harness.run_turn("最近睡不着", proposal(RouterAction.START_SCALE, scale="PHQ-9", needs_rag=False))
        old = harness.controller.start_generation()
        result, _ = harness.run_turn("做什么都没劲，几乎每天", proposal(RouterAction.CHAT, needs_rag=False))
        assert result.turn_decision.action is TurnAction.CONTINUE_SCALE
        harness.controller.cancel_generation(old.generation_id, reason="late audio")
        assert harness.pipeline.scale_runtime.answers_by_scale["PHQ-9"][1] == 3
    finally:
        harness.shutdown()
