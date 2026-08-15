"""Phase 8 successful conversation and authority-chain scenarios."""

from __future__ import annotations

from threading import Event, Thread
from typing import Any

import pytest

from app.contracts import (
    ContinueChatCommand,
    EndSessionCommand,
    PlayRelaxationCommand,
    RelaxationFinishedCommand,
)
from conversation.contracts import RouterAction, TurnAction
from conversation.delivery import SentenceReady
from core.session_fsm import SessionState
from core.types import EndType
from services.scales import get_scale_manager

from tests.e2e.fixtures import (
    ScenarioHarness,
    proposal,
    start_session,
)


@pytest.fixture
def harness():
    value = ScenarioHarness(start_round=0)
    try:
        yield value
    finally:
        value.shutdown()


def _complete_scale(harness: ScenarioHarness, scale_name: str):
    definition = get_scale_manager().get_scale_definition(scale_name)
    assert definition is not None
    harness.report.round_count = 6
    harness.run_turn(
        "开始这个量表",
        proposal(RouterAction.START_SCALE, scale=scale_name, needs_rag=True),
    )
    for _item in range(definition.item_count):
        result, _generation = harness.run_turn(
            "没有",
            proposal(RouterAction.CHAT, needs_rag=False),
        )
        assert result.turn_decision is not None
        assert result.turn_decision.action in {
            TurnAction.CONTINUE_SCALE,
            TurnAction.CHAT,
        }
    runtime = harness.pipeline.scale_runtime.snapshot()
    assert scale_name in runtime.completed_scales
    assert runtime.active_scale is None
    return definition


def test_a1_ordinary_chat_without_rag_uses_one_decision_and_delivered_history(harness):
    result, generation = harness.run_turn(
        "今天心情还可以。",
        proposal(RouterAction.CHAT, needs_rag=False),
    )

    assert result.turn_decision.action is TurnAction.CHAT
    assert len(harness.trace.policy_calls) == 1
    assert harness.trace.rag_queries == []
    assert harness.trace.visible_sentences
    assert harness.trace.finalized_generations[-1] == (
        generation,
        harness.trace.visible_sentences[0].text,
    )
    assert harness.llm.conversation_history[-1]["role"] == "assistant"


def test_a2_approved_rag_reaches_language_context_once(harness):
    result, _generation = harness.run_turn(
        "最近睡眠很差。",
        proposal(RouterAction.CHAT, needs_rag=True),
    )

    assert result.turn_decision.needs_rag is True
    assert len(harness.rag.calls) == 1
    assert harness.rag.calls[0][1] is True
    assert "知识库" in harness.llm.calls[-1]["system_suffix"]


def test_a3_psychology_keywords_do_not_override_needs_rag_false(harness):
    result, _generation = harness.run_turn(
        "我最近失眠，也有点焦虑。",
        proposal(RouterAction.CHAT, needs_rag=False),
    )

    assert result.turn_decision.needs_rag is False
    assert harness.rag.calls == []


def test_a4_simple_wording_with_needs_rag_true_retrieves(harness):
    result, _generation = harness.run_turn(
        "嗯。",
        proposal(RouterAction.CHAT, needs_rag=True),
    )

    assert result.turn_decision.needs_rag is True
    assert len(harness.rag.calls) == 1


def test_b1_complete_phq9_uses_runtime_definition_and_report_snapshot(harness):
    definition = _complete_scale(harness, "PHQ-9")

    runtime = harness.pipeline.scale_runtime.snapshot()
    results = harness.pipeline.get_scale_results()
    assert definition.item_count == 9
    assert all(
        decision.action in {TurnAction.START_SCALE, TurnAction.CONTINUE_SCALE}
        for decision in harness.trace.turn_decisions[: definition.item_count + 1]
    )
    assert dict(runtime.answers_by_scale["PHQ-9"]) == {
        item: 0 for item in range(1, definition.item_count + 1)
    }
    assert "PHQ-9" in results
    assert results["PHQ-9"]["total_score"] == 0


def test_b2_complete_gad7_uses_canonical_item_count_and_score_range(harness):
    definition = _complete_scale(harness, "GAD-7")

    runtime = harness.pipeline.scale_runtime.snapshot()
    assert definition.item_count == 7
    assert set(runtime.answers_by_scale["GAD-7"]) == set(range(1, 8))
    assert set(runtime.answers_by_scale["GAD-7"].values()) <= set(definition.legal_scores)


def test_b3_complete_simplified_pcl5_does_not_assume_twenty_items(harness):
    definition = _complete_scale(harness, "PCL-5")

    runtime = harness.pipeline.scale_runtime.snapshot()
    assert definition.item_count == 8
    assert definition.item_count != 20
    assert set(runtime.answers_by_scale["PCL-5"]) == set(range(1, 9))
    assert set(runtime.answers_by_scale["PCL-5"].values()) <= set(definition.legal_scores)


def test_b4_ambiguous_answer_requests_clarification_without_advancing(harness):
    harness.report.round_count = 6
    harness.run_turn(
        "开始量表",
        proposal(RouterAction.START_SCALE, scale="PHQ-9", needs_rag=True),
    )
    result, _generation = harness.run_turn(
        "有时候吧",
        proposal(RouterAction.CHAT, needs_rag=False),
    )

    runtime = harness.pipeline.scale_runtime.snapshot()
    assert result.turn_decision.action is TurnAction.CONTINUE_SCALE
    assert runtime.active_scale == "PHQ-9"
    assert runtime.current_item == 1
    assert runtime.waiting_for_answer is True
    assert dict(runtime.answers_by_scale["PHQ-9"]) == {}


def test_b5_pause_and_resume_uses_actual_first_unanswered_item(harness):
    harness.report.round_count = 6
    harness.run_turn(
        "开始量表",
        proposal(RouterAction.START_SCALE, scale="PHQ-9", needs_rag=True),
    )
    harness.run_turn("做什么都没劲，几乎每天", proposal(RouterAction.CHAT))
    before_pause = harness.pipeline.scale_runtime.snapshot()
    assert before_pause.current_item == 2

    paused, _generation = harness.run_turn(
        "我想放松一下",
        proposal(
            RouterAction.RECOMMEND_RELAXATION,
            intervention="breathing",
            needs_rag=False,
        ),
    )
    assert paused.turn_decision.action is TurnAction.RECOMMEND_RELAXATION
    assert harness.pipeline.scale_runtime.snapshot().paused is True

    resumed_question = harness.pipeline.resume_scale_after_relaxation()
    resumed = harness.pipeline.scale_runtime.snapshot()
    assert resumed_question
    assert resumed.current_item == 2
    assert resumed.waiting_for_answer is True
    assert resumed.resume_item is None


def test_b6_completed_scale_is_read_by_report_projection_not_ui_state(harness):
    _complete_scale(harness, "PHQ-9")

    result = harness.pipeline.get_scale_results()
    assert result["PHQ-9"]["total_items"] == 9
    assert result["PHQ-9"]["total_score"] == 0
    assert not hasattr(harness.pipeline, "_active_scale")


def test_c1_explicit_relaxation_is_allowed_before_proactive_threshold(harness):
    result, _generation = harness.run_turn(
        "我想做个放松练习",
        proposal(
            RouterAction.RECOMMEND_RELAXATION,
            intervention="breathing",
            needs_rag=False,
        ),
    )

    assert harness.report.round_count == 1
    assert result.turn_decision.action is TurnAction.RECOMMEND_RELAXATION
    assert result.turn_decision.reason == "user_relaxation_request"


def test_c2_proactive_relaxation_at_round_seven_is_rejected(harness):
    harness.report.round_count = 6
    result, _generation = harness.run_turn(
        "最近有点累",
        proposal(
            RouterAction.RECOMMEND_RELAXATION,
            intervention="breathing",
            needs_rag=False,
        ),
    )

    assert result.turn_decision.action is TurnAction.CHAT
    assert result.turn_decision.reason == "proactive_relaxation_before_min_rounds"


def test_c3_proactive_relaxation_at_round_eight_is_approved_once(harness):
    harness.report.round_count = 7
    result, _generation = harness.run_turn(
        "最近有点累",
        proposal(
            RouterAction.RECOMMEND_RELAXATION,
            intervention="breathing",
            needs_rag=False,
        ),
    )

    assert result.turn_decision.action is TurnAction.RECOMMEND_RELAXATION
    assert result.turn_decision.reason == "proactive_relaxation_accepted"
    assert harness.pipeline._proactive_relaxation_offered is True


def test_c4_second_proactive_relaxation_is_rejected_but_explicit_request_is_distinct(harness):
    harness.report.round_count = 7
    harness.run_turn(
        "最近有点累",
        proposal(
            RouterAction.RECOMMEND_RELAXATION,
            intervention="breathing",
            needs_rag=False,
        ),
    )
    result, _generation = harness.run_turn(
        "最近还是有点累",
        proposal(
            RouterAction.RECOMMEND_RELAXATION,
            intervention="breathing",
            needs_rag=False,
        ),
    )

    assert result.turn_decision.action is TurnAction.CHAT
    assert result.turn_decision.reason == "proactive_relaxation_already_offered"


def test_c5_waiting_scale_answer_blocks_proactive_relaxation(harness):
    harness.report.round_count = 6
    harness.run_turn(
        "开始量表",
        proposal(RouterAction.START_SCALE, scale="PHQ-9", needs_rag=True),
    )
    result, _generation = harness.run_turn(
        "最近有点累",
        proposal(
            RouterAction.RECOMMEND_RELAXATION,
            intervention="breathing",
            needs_rag=False,
        ),
    )

    runtime = harness.pipeline.scale_runtime.snapshot()
    assert result.turn_decision.action is TurnAction.CONTINUE_SCALE
    assert runtime.current_item == 1
    assert runtime.waiting_for_answer is True


def test_d1_explicit_game_request_is_the_only_game_authority(harness):
    result, _generation = harness.run_turn(
        "我想玩个游戏",
        proposal(
            RouterAction.RECOMMEND_GAME,
            intervention="game",
            needs_rag=False,
        ),
    )

    assert result.turn_decision.action is TurnAction.RECOMMEND_GAME
    assert result.intent == "entertainment"
    assert all("[REC_" not in event.text for event in harness.trace.visible_sentences)


def test_d2_boredom_does_not_implicitly_start_game(harness):
    result, _generation = harness.run_turn(
        "我好无聊",
        proposal(RouterAction.RECOMMEND_GAME, intervention="game"),
    )

    assert result.turn_decision.action is TurnAction.CHAT
    assert result.turn_decision.reason == "game_requires_explicit_request"


def test_e1_explicit_end_wins_over_conflicting_router_proposal(harness):
    result, _generation = harness.run_turn(
        "结束",
        proposal(RouterAction.RECOMMEND_GAME, intervention="game"),
    )
    events: list[Any] = []
    engine = start_session(events, "E1")
    try:
        engine.process_command(
            EndSessionCommand(end_type=EndType.QUIT, allow_force_relaxation=False)
        )
        assert result.turn_decision.action is TurnAction.END_SESSION
        assert result.end_type == "quit"
        assert engine.state is SessionState.SESSION_ENDING
        assert not hasattr(result, "forced_relaxation")
    finally:
        engine.shutdown()


def test_e2_explicit_end_during_scale_preserves_partial_runtime(harness):
    harness.report.round_count = 6
    harness.run_turn(
        "开始量表",
        proposal(RouterAction.START_SCALE, scale="PHQ-9", needs_rag=True),
    )
    before = harness.pipeline.scale_runtime.snapshot()
    result, _generation = harness.run_turn(
        "不想聊了",
        proposal(RouterAction.CHAT),
    )
    after = harness.pipeline.scale_runtime.snapshot()

    assert result.turn_decision.action is TurnAction.END_SESSION
    assert after.active_scale == before.active_scale
    assert after.current_item == before.current_item
    assert dict(after.answers_by_scale["PHQ-9"]) == dict(before.answers_by_scale["PHQ-9"])


def test_e3_positive_feedback_does_not_end_session(harness):
    result, _generation = harness.run_turn("好多了", proposal(RouterAction.END_SESSION))

    assert result.turn_decision.action is TurnAction.CHAT
    assert result.end_type is None


def test_e4_end_during_relaxation_media_is_deferred_then_resumed():
    events: list[Any] = []
    engine = start_session(events, "E4")
    try:
        engine.process_command(PlayRelaxationCommand(relaxation="breathing"))
        assert engine.state is SessionState.VIDEO_PLAYING
        engine.process_command(
            EndSessionCommand(end_type=EndType.QUIT, allow_force_relaxation=False)
        )
        assert engine.state is SessionState.VIDEO_PLAYING
        engine.process_command(RelaxationFinishedCommand(completed=True))
        assert engine.state is SessionState.SESSION_ENDING
    finally:
        engine.shutdown()


def test_e5_report_is_recorded_before_farewell_delivery():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "ui" / "main_window.py").read_text(encoding="utf-8")
    body = source.split("def generate_farewell_and_reports", 1)[1]
    report_position = body.index("self.report_service.generate_researcher_report")
    pdf_position = body.index("self._generate_and_save_pdf")
    farewell_position = body.index("report/PDF done, now playing farewell TTS")

    assert report_position < pdf_position < farewell_position


def test_f1_timeout_emits_one_choice_without_silent_end():
    events: list[Any] = []
    engine = start_session(events, "F1")
    try:
        assert engine.should_emit_time_limit_ask(45.1, 45) is True
        assert engine.should_emit_time_limit_ask(45.2, 45) is False
        assert engine.state is SessionState.CHATTING
    finally:
        engine.shutdown()


def test_f2_timeout_continue_suppresses_repeated_choice():
    events: list[Any] = []
    engine = start_session(events, "F2")
    try:
        assert engine.should_emit_time_limit_ask(45.1, 45) is True
        engine.process_command(ContinueChatCommand())
        engine.acknowledge_time_limit_continue()
        assert engine.should_emit_time_limit_ask(46.0, 45) is False
    finally:
        engine.shutdown()


def test_f3_timeout_end_choice_ends_once_without_relaxation():
    events: list[Any] = []
    engine = start_session(events, "F3")
    try:
        assert engine.should_emit_time_limit_ask(45.1, 45) is True
        engine.process_command(
            EndSessionCommand(end_type=EndType.QUIT, allow_force_relaxation=False)
        )
        assert engine.state is SessionState.SESSION_ENDING
        assert not any(getattr(event, "kind", "") == "relaxation_recommended" for event in events)
    finally:
        engine.shutdown()


def test_g1_sentence_one_reaches_ui_and_tts_before_provider_finishes(harness):
    first_ready = Event()
    release_provider = Event()

    class BlockingLLM:
        conversation_history = []

        def chat(self, text, system_suffix="", *, commit_history=False):
            self.conversation_history.append({"role": "user", "content": text})
            yield "第一句。"
            first_ready.set()
            assert release_provider.wait(timeout=2.0)
            yield "第二句。"

    harness.llm = BlockingLLM()
    harness.pipeline.llm = harness.llm
    record = harness.controller.start_generation()
    result_box: list[Any] = []

    def execute():
        from services.pipeline import PipelineConfig

        result_box.append(
            harness.pipeline.execute(
                PipelineConfig(
                    user_text="继续说",
                    router_proposal=proposal(RouterAction.CHAT, needs_rag=False),
                    generation_id=record.generation_id,
                ),
                harness.emit,
            )
        )

    worker = Thread(target=execute, daemon=True)
    worker.start()
    assert first_ready.wait(timeout=2.0)
    assert [event.text for event in harness.trace.visible_sentences] == ["第一句。"]
    assert harness.tts.started.wait(timeout=2.0)
    assert harness.trace.tts_calls == ["第一句。"]
    release_provider.set()
    worker.join(timeout=3.0)
    assert not worker.is_alive()
    assert [event.text for event in harness.trace.visible_sentences] == ["第一句。", "第二句。"]
    assert result_box and result_box[0].turn_decision.action is TurnAction.CHAT


def test_g2_new_turn_cancels_old_generation_and_drains_tts_queue(harness):
    harness.tts.block = True
    harness.pipeline.delivery_queue.start()
    old = harness.controller.start_generation()
    assert harness.pipeline.delivery_queue.enqueue(SentenceReady(old.generation_id, 0, "正在播放。"))
    assert harness.pipeline.delivery_queue.enqueue(SentenceReady(old.generation_id, 1, "不应播放。"))
    assert harness.tts.started.wait(timeout=2.0)

    new = harness.controller.start_generation()
    assert new.generation_id == old.generation_id + 1
    assert not harness.controller.is_current(old.generation_id)
    harness.tts.release.set()
    assert harness.trace.tts_stop_calls >= 1
    assert harness.trace.tts_calls == ["正在播放。"]


def test_g3_late_provider_ui_tts_and_history_callbacks_are_stale(harness):
    old = harness.controller.start_generation()
    ledger = harness.pipeline.delivery_ledger
    ledger.record_generated(old.generation_id, "旧尾巴")
    new = harness.controller.start_generation()

    assert not ledger.commit_visible(SentenceReady(old.generation_id, 0, "旧句。"))
    assert not harness.pipeline.delivery_queue.enqueue(SentenceReady(old.generation_id, 0, "旧音频。"))
    assert ledger.finalize_history(old.generation_id, harness.llm, harness.data) == ""
    assert new.generation_id > old.generation_id
    assert not any(write["text"] == "旧句。" for write in harness.trace.data_manager_writes)


def test_g4_cancelled_generated_tail_is_excluded_from_delivered_history(harness):
    record = harness.controller.start_generation()
    ledger = harness.pipeline.delivery_ledger
    ledger.record_generated(record.generation_id, "A+B+C")
    assert ledger.commit_visible(SentenceReady(record.generation_id, 0, "A"))
    harness.controller.cancel_generation(record.generation_id, reason="interrupt")

    assert ledger.finalize_history(record.generation_id, harness.llm, harness.data) == "A"
    assert ledger.generated_text(record.generation_id) == "A+B+C"
    assert harness.llm.conversation_history[-1]["content"] == "A"


def test_g5_completed_generation_finalizes_delivered_text_once(harness):
    record = harness.controller.start_generation()
    ledger = harness.pipeline.delivery_ledger
    ledger.commit_visible(SentenceReady(record.generation_id, 0, "第一句。"))
    ledger.commit_visible(SentenceReady(record.generation_id, 1, "第二句。"))

    assert ledger.finalize_history(record.generation_id, harness.llm, harness.data) == "第一句。第二句。"
    assert ledger.finalize_history(record.generation_id, harness.llm, harness.data) == "第一句。第二句。"
    assert [x for x in harness.trace.data_manager_writes if x["role"] == "assistant"] == [
        {"role": "assistant", "text": "第一句。第二句。"}
    ]


def test_g6_cancel_before_visible_text_creates_no_phantom_assistant_turn(harness):
    record = harness.controller.start_generation()
    harness.pipeline.delivery_ledger.record_generated(record.generation_id, "看不见的尾巴")
    harness.controller.cancel_generation(record.generation_id, reason="new turn")

    assert harness.pipeline.delivery_ledger.finalize_history(
        record.generation_id, harness.llm, harness.data
    ) == ""
    assert not any(write["role"] == "assistant" for write in harness.trace.data_manager_writes)


def test_h1_session_b_does_not_inherit_scale_allowance_timeout_or_generation(harness):
    harness.pipeline.scale_runtime.start("PHQ-9")
    harness.pipeline._proactive_relaxation_offered = True
    harness.report.time_limit_prompt_shown = True
    old = harness.controller.start_generation()
    harness.controller.cancel_generation(old.generation_id, reason="session reset")

    harness.pipeline.reset_session()
    harness.report.start_session()
    new = harness.controller.start_generation()
    runtime = harness.pipeline.scale_runtime.snapshot()

    assert new.generation_id > old.generation_id
    assert runtime.active_scale is None
    assert runtime.answers_by_scale == {}
    assert runtime.completed_scales == ()
    assert harness.pipeline._proactive_relaxation_offered is False
    assert harness.report.time_limit_prompt_shown is False


def test_h2_late_session_a_callback_cannot_write_session_b_history(harness):
    old = harness.controller.start_generation()
    ledger = harness.pipeline.delivery_ledger
    ledger.commit_visible(SentenceReady(old.generation_id, 0, "A 会话旧句。"))
    harness.controller.start_generation()

    assert ledger.finalize_history(old.generation_id, harness.llm, harness.data) == ""
    assert not any(
        write["text"] == "A 会话旧句。" for write in harness.trace.data_manager_writes
    )
