"""Tests for core.session_fsm and core.end_guard — canonical import paths.

The shim path (services.session_orchestrator / services.session_end_controller)
is still covered by test_session_end_controller.py and the app itself.
"""

import pytest

from core.types import EndType
from core.session_fsm import SessionContext, SessionOrchestrator, SessionState
from core.end_guard import EndGuardResult, SessionEndController


@pytest.fixture
def orchestrator():
    orch = SessionOrchestrator()
    orch.reset()  # start in CHATTING like a live session
    return orch


class TestTransitions:
    def test_valid_transition_chat_to_ending(self, orchestrator):
        assert orchestrator.transition_to(SessionState.SESSION_ENDING) is True
        assert orchestrator.state == SessionState.SESSION_ENDING

    def test_invalid_transition_rejected_state_unchanged(self, orchestrator):
        assert orchestrator.transition_to(SessionState.POST_RELAXATION) is False
        assert orchestrator.state == SessionState.CHATTING

    def test_full_relaxation_path(self, orchestrator):
        assert orchestrator.transition_to(SessionState.RELAXATION_RECOMMENDED)
        assert orchestrator.transition_to(SessionState.VIDEO_PLAYING)
        assert orchestrator.transition_to(SessionState.POST_RELAXATION)
        assert orchestrator.transition_to(SessionState.CHATTING)

    def test_video_playing_only_exits_to_post_relaxation(self, orchestrator):
        orchestrator.transition_to(SessionState.VIDEO_PLAYING)
        assert orchestrator.transition_to(SessionState.SESSION_ENDING) is False
        assert orchestrator.transition_to(SessionState.POST_RELAXATION) is True


class TestCapabilityChecks:
    def test_can_start_pipeline_only_in_chatting(self, orchestrator):
        assert orchestrator.can_start_pipeline() is True
        orchestrator.transition_to(SessionState.VIDEO_PLAYING)
        assert orchestrator.can_start_pipeline() is False

    def test_can_play_video(self, orchestrator):
        assert orchestrator.can_play_video() is True
        orchestrator.transition_to(SessionState.RELAXATION_RECOMMENDED)
        assert orchestrator.can_play_video() is True
        orchestrator.transition_to(SessionState.VIDEO_PLAYING)
        assert orchestrator.can_play_video() is False

    def test_is_session_active(self, orchestrator):
        assert orchestrator.is_session_active() is True
        orchestrator.transition_to(SessionState.SESSION_ENDING)
        assert orchestrator.is_session_active() is False


class TestEvaluateSessionEnd:
    def test_goal_achieved_forces_relaxation_once(self, orchestrator):
        action, data = orchestrator.evaluate_session_end(EndType.GOAL_ACHIEVED)
        assert action == "force_relaxation"
        assert data["relaxation_tag"] == "呼吸"
        assert orchestrator.state == SessionState.RELAXATION_RECOMMENDED

    def test_second_end_goes_straight_to_reports(self, orchestrator):
        orchestrator.evaluate_session_end(EndType.GOAL_ACHIEVED)
        # back to chatting, then end again — no second forced relaxation
        orchestrator.ctx.state = SessionState.CHATTING
        action, _ = orchestrator.evaluate_session_end(EndType.GOAL_ACHIEVED)
        assert action == "generate_reports"
        assert orchestrator.state == SessionState.SESSION_ENDING

    def test_quit_never_forces_relaxation(self, orchestrator):
        action, _ = orchestrator.evaluate_session_end(EndType.QUIT)
        assert action == "generate_reports"

    def test_already_relaxed_skips_force(self, orchestrator):
        orchestrator.ctx.current_relaxation_type = "呼吸放松训练"
        action, _ = orchestrator.evaluate_session_end(EndType.GOAL_ACHIEVED)
        assert action == "generate_reports"

    def test_custom_relaxation_tag_passthrough(self, orchestrator):
        _, data = orchestrator.evaluate_session_end(EndType.TIME_LIMIT, relaxation_tag="肌肉")
        assert data["relaxation_tag"] == "肌肉"


class TestReset:
    def test_reset_puts_session_in_chatting(self):
        orch = SessionOrchestrator()
        assert orch.state == SessionState.IDLE
        orch.reset()
        assert orch.state == SessionState.CHATTING

    def test_reset_clears_context(self, orchestrator):
        orchestrator.ctx.has_forced_relaxation_rec = True
        orchestrator.ctx.current_relaxation_type = "冥想"
        orchestrator.reset()
        assert orchestrator.ctx.has_forced_relaxation_rec is False
        assert orchestrator.ctx.current_relaxation_type is None


class TestEndGuardCanonicalPath:
    def test_begin_once(self):
        ctrl = SessionEndController()
        first = ctrl.begin()
        assert isinstance(first, EndGuardResult)
        assert first.accepted is True
        assert ctrl.begin().accepted is False
        assert ctrl.is_ending is True

    def test_defer_releases(self):
        ctrl = SessionEndController()
        ctrl.begin()
        ctrl.defer_for_relaxation()
        assert ctrl.begin().accepted is True
