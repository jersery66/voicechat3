"""Phase 6 scale pause/resume and Center context contracts."""

from __future__ import annotations

from types import SimpleNamespace

from app.engine import SessionEngine
from app.contracts import StartSessionCommand, SubjectInfo
from assessment.scale_runtime import ScaleRuntime
from core.session_fsm import SessionState
from relaxation.catalog import build_default_catalog
from relaxation.runtime import RelaxationRuntime
from relaxation.return_context import RelaxationReturnContext
from tests.e2e.fixtures import ScenarioHarness, proposal
from conversation.contracts import RouterAction, TurnAction
from ui.main_window import MainWindow


def test_return_context_is_frozen_and_contains_no_scale_answers_or_scores():
    context = RelaxationReturnContext(
        source="CENTER",
        scale_was_paused=True,
        scale_name="PHQ-9",
        conversation_anchor="最近睡不好。",
    )
    assert context.scale_name == "PHQ-9"
    assert not hasattr(context, "answers_by_scale")
    assert not hasattr(context, "score")


def _window_stub():
    engine = SessionEngine()
    engine.process_command(StartSessionCommand(subject=SubjectInfo(subject_id="PHASE6")))
    runtime = ScaleRuntime()
    window = MainWindow.__new__(MainWindow)
    window.session_engine = engine
    window.pipeline = SimpleNamespace(
        scale_runtime=runtime,
        get_active_scale_question_text=lambda: "当前量表问题",
    )
    window.relaxation_catalog = build_default_catalog()
    window.relaxation_runtime = RelaxationRuntime(window.relaxation_catalog)
    window._relaxation_return_context = None
    window._relaxation_center_dialog = None
    window._relaxation_game_dialog = None
    window._pending_leisure_game_start = None
    window._pending_relaxation_preference = None
    window._engine_submit = engine.process_command
    window._engine_can_play_video = lambda: engine.can_play_video()
    window.control_panel = SimpleNamespace(stop_all_blinks=lambda: None, set_status=lambda _text: None)
    window.llm_service = SimpleNamespace(
        conversation_history=[{"role": "user", "content": "最近晚上睡不好。"}]
    )
    window.chat_panel = SimpleNamespace(add_system_message=lambda _text: None)
    window.tts_service = None
    return window, engine, runtime


def test_center_entry_pauses_active_scale_and_projects_inactive_for_media():
    window, engine, runtime = _window_stub()
    runtime.start("PHQ-9")

    assert window._prepare_relaxation_center_entry("CENTER") is True

    assert runtime.snapshot().paused is True
    assert engine.snapshot().scale_active is False
    assert window._relaxation_return_context.scale_was_paused is True
    assert window._relaxation_return_context.conversation_anchor == "最近晚上睡不好。"
    engine.shutdown()


def test_paused_scale_allows_leisure_start_after_projection():
    window, engine, runtime = _window_stub()
    runtime.start("PHQ-9")
    window._prepare_relaxation_center_entry("CENTER")

    assert window._start_relaxation_game("bubble_pop") is True
    assert engine.state is SessionState.VIDEO_PLAYING
    assert engine.snapshot().playback_kind == "leisure"
    assert runtime.snapshot().paused is True
    engine.shutdown()


def test_core_shortcut_while_scale_active_uses_same_center_pause_helper():
    window, engine, runtime = _window_stub()
    runtime.start("PHQ-9")
    window._play_relaxation_video = lambda _content_id: None

    assert window._start_core_relaxation("breathing", "DIRECT_SHORTCUT") is True

    assert runtime.snapshot().paused is True
    assert engine.snapshot().scale_active is False
    engine.shutdown()


def test_center_return_resumes_runtime_first_unanswered_item():
    window, engine, runtime = _window_stub()
    runtime.start("PHQ-9")
    runtime.accept_answer(scale_name="PHQ-9", item=1, score=2)
    window._prepare_relaxation_center_entry("CENTER")
    window._on_center_returned()

    assert runtime.snapshot().paused is False
    assert runtime.snapshot().current_item == 2
    assert runtime.snapshot().answers_by_scale["PHQ-9"][1] == 2
    assert window._relaxation_return_context is None
    engine.shutdown()


def test_game_completion_does_not_resume_until_explicit_center_return():
    window, engine, scale_runtime = _window_stub()
    scale_runtime.start("PHQ-9")
    window._prepare_relaxation_center_entry("CENTER")
    window.relaxation_runtime.enter_center()
    window.relaxation_runtime.start_content("bubble_pop")
    window._relaxation_game_dialog = SimpleNamespace(close=lambda: None)
    window.report_service = SimpleNamespace(activity_log=[])

    window._on_relaxation_game_finished("bubble_pop", completed=True)

    assert window.relaxation_runtime.snapshot().state.value == "CENTER"
    assert window.pipeline.scale_runtime.snapshot().paused is True
    window._on_center_returned()
    assert window.pipeline.scale_runtime.snapshot().paused is False
    engine.shutdown()


def test_compound_answer_commits_before_explicit_rest_pause():
    harness = ScenarioHarness(start_round=6)
    try:
        harness.pipeline.scale_runtime.start("PHQ-9")
        harness.pipeline.scale_runtime.accept_answer(scale_name="PHQ-9", item=1, score=1)
        harness.pipeline.scale_runtime.present_current_item()
        harness.pipeline.scale_runtime.accept_answer(scale_name="PHQ-9", item=2, score=1)
        harness.pipeline.scale_runtime.present_current_item()
        result, _ = harness.run_turn(
            "几乎每天，先让我休息一下",
            proposal(RouterAction.CHAT, needs_rag=False),
        )
        runtime = harness.pipeline.scale_runtime.snapshot()
        assert result.turn_decision.action is TurnAction.RECOMMEND_RELAXATION
        assert dict(runtime.answers_by_scale["PHQ-9"]) == {1: 1, 2: 1, 3: 3}
        assert runtime.paused is False
        harness.pipeline.scale_runtime.pause()
        runtime = harness.pipeline.scale_runtime.snapshot()
        assert runtime.paused is True
        assert runtime.current_item == 4
    finally:
        harness.shutdown()


def test_ambiguous_answer_and_explicit_rest_keeps_same_item_unanswered():
    harness = ScenarioHarness(start_round=6)
    try:
        harness.pipeline.scale_runtime.start("PHQ-9")
        harness.pipeline.scale_runtime.accept_answer(scale_name="PHQ-9", item=1, score=1)
        harness.pipeline.scale_runtime.present_current_item()
        harness.pipeline.scale_runtime.accept_answer(scale_name="PHQ-9", item=2, score=1)
        harness.pipeline.scale_runtime.present_current_item()
        result, _ = harness.run_turn(
            "这个我说不好，先让我休息一下",
            proposal(RouterAction.CHAT, needs_rag=False),
        )
        runtime = harness.pipeline.scale_runtime.snapshot()
        assert result.turn_decision.action is TurnAction.RECOMMEND_RELAXATION
        assert dict(runtime.answers_by_scale["PHQ-9"]) == {1: 1, 2: 1}
        assert runtime.paused is False
        harness.pipeline.scale_runtime.pause()
        runtime = harness.pipeline.scale_runtime.snapshot()
        assert runtime.paused is True
        assert runtime.current_item == 3
    finally:
        harness.shutdown()
