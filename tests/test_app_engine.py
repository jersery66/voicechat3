"""Tests for app.engine — SessionEngine single-writer facade."""

import time

import pytest

from core.types import EndType
from core.session_fsm import SessionState
from app.contracts import (
    AcknowledgeTimeLimitCommand,
    ContinueChatCommand,
    ContinueOrEndAskEvent,
    EndSessionCommand,
    ErrorEvent,
    PlayRelaxationCommand,
    RelaxationFinishedCommand,
    SessionEndingEvent,
    StartSessionCommand,
    StateChangedEvent,
    SubjectInfo,
)
from app.engine import SessionEngine


@pytest.fixture
def events():
    return []


@pytest.fixture
def engine(events):
    eng = SessionEngine(emit=events.append)
    yield eng
    eng.shutdown()


def start(engine):
    engine.process_command(StartSessionCommand(subject=SubjectInfo(subject_id="S001")))


class TestStartSession:
    def test_enters_chatting(self, engine, events):
        start(engine)
        assert engine.state == SessionState.CHATTING
        assert any(isinstance(e, StateChangedEvent) and e.state == "CHATTING"
                   for e in events)

    def test_restart_resets_flags(self, engine, events):
        start(engine)
        engine.process_command(EndSessionCommand(end_type=EndType.QUIT))
        assert engine.is_ending
        start(engine)
        assert not engine.is_ending
        assert engine.state == SessionState.CHATTING


class TestEndSession:
    def test_goal_achieved_does_not_invent_relaxation(self, engine, events):
        start(engine)
        engine.process_command(EndSessionCommand(end_type=EndType.GOAL_ACHIEVED))
        assert any(isinstance(e, SessionEndingEvent) for e in events)
        assert engine.state == SessionState.SESSION_ENDING

    def test_relaxation_hint_does_not_trigger_media(self, engine, events):
        start(engine)
        engine.process_command(EndSessionCommand(
            end_type=EndType.GOAL_ACHIEVED, relaxation_hint="meditation"))
        assert any(isinstance(e, SessionEndingEvent) for e in events)
        assert engine.state == SessionState.SESSION_ENDING

    def test_second_end_proceeds_to_reports(self, engine, events):
        start(engine)
        engine.process_command(EndSessionCommand(end_type=EndType.GOAL_ACHIEVED))
        events.clear()
        engine.process_command(EndSessionCommand(end_type=EndType.GOAL_ACHIEVED))
        assert not any(isinstance(e, SessionEndingEvent) for e in events)
        assert engine.state == SessionState.SESSION_ENDING

    @pytest.mark.parametrize("kwargs", [
        {"end_type": EndType.QUIT},
        {"end_type": EndType.INVALID},
        {"end_type": EndType.GOAL_ACHIEVED, "allow_force_relaxation": False},
    ])
    def test_no_force_paths(self, engine, events, kwargs):
        start(engine)
        engine.process_command(EndSessionCommand(**kwargs))
        assert any(isinstance(e, SessionEndingEvent) for e in events)
        assert engine.state == SessionState.SESSION_ENDING

    def test_duplicate_end_ignored(self, engine, events):
        start(engine)
        engine.process_command(EndSessionCommand(end_type=EndType.QUIT))
        events.clear()
        engine.process_command(EndSessionCommand(end_type=EndType.QUIT))
        assert not any(isinstance(e, SessionEndingEvent) for e in events)

    def test_played_relaxation_blocks_force_completed(self, engine, events):
        """Legacy gate: any played relaxation (completed=True) blocks forcing."""
        start(engine)
        engine.process_command(PlayRelaxationCommand(relaxation="muscle"))
        engine.process_command(RelaxationFinishedCommand(completed=True))
        events.clear()
        engine.process_command(EndSessionCommand(end_type=EndType.GOAL_ACHIEVED))
        assert any(isinstance(e, SessionEndingEvent) for e in events)
        assert not any(getattr(e, "kind", "") == "relaxation_recommended" for e in events)

    def test_played_relaxation_blocks_force_exited_early(self, engine, events):
        """completed=False (user exited the video early) must also block the
        forced relaxation — matches legacy ctx.current_relaxation_type gate."""
        start(engine)
        engine.process_command(PlayRelaxationCommand(relaxation="muscle"))
        engine.process_command(RelaxationFinishedCommand(completed=False))
        events.clear()
        engine.process_command(EndSessionCommand(end_type=EndType.GOAL_ACHIEVED))
        assert any(isinstance(e, SessionEndingEvent) for e in events)
        assert not any(getattr(e, "kind", "") == "relaxation_recommended" for e in events)


class TestEndDuringVideo:
    """Regression: end_session while VIDEO_PLAYING used to deadlock the FSM."""

    def test_end_deferred_until_video_finishes(self, engine, events):
        start(engine)
        engine.process_command(PlayRelaxationCommand(relaxation="breathing"))
        assert engine.state == SessionState.VIDEO_PLAYING

        engine.process_command(EndSessionCommand(end_type=EndType.GOAL_ACHIEVED))
        # no end flow yet, guard released, state untouched
        assert not any(isinstance(e, SessionEndingEvent) for e in events)
        assert engine.state == SessionState.VIDEO_PLAYING
        assert not engine.is_ending

        # video finishes -> deferred end resumes straight to reports
        events.clear()
        engine.process_command(RelaxationFinishedCommand(completed=True))
        assert any(isinstance(e, SessionEndingEvent) for e in events)
        assert engine.state == SessionState.SESSION_ENDING

    def test_deferred_end_survives_mark_ended(self, engine, events):
        start(engine)
        engine.process_command(PlayRelaxationCommand(relaxation="muscle"))
        engine.process_command(EndSessionCommand(end_type=EndType.QUIT))
        engine.process_command(RelaxationFinishedCommand(completed=True))
        engine.mark_session_ended()
        assert engine.state == SessionState.SESSION_ENDED
        assert not engine.is_ending

    def test_start_session_clears_pending_end(self, engine, events):
        start(engine)
        engine.process_command(PlayRelaxationCommand(relaxation="breathing"))
        engine.process_command(EndSessionCommand(end_type=EndType.QUIT))
        start(engine)  # operator starts a fresh session instead
        engine.process_command(RelaxationFinishedCommand(completed=True))
        assert not any(isinstance(e, SessionEndingEvent) for e in events)


class TestRelaxationFlow:
    def test_full_relaxation_cycle(self, engine, events):
        start(engine)
        engine.process_command(PlayRelaxationCommand(relaxation="breathing"))
        assert engine.state == SessionState.VIDEO_PLAYING
        engine.process_command(RelaxationFinishedCommand(completed=True))
        assert engine.state == SessionState.POST_RELAXATION
        assert any(isinstance(e, ContinueOrEndAskEvent) for e in events)
        engine.process_command(ContinueChatCommand())
        assert engine.state == SessionState.CHATTING

    def test_play_relaxation_rejected_outside_allowed_states(self, engine, events):
        start(engine)
        engine.process_command(EndSessionCommand(end_type=EndType.QUIT))
        events.clear()
        engine.process_command(PlayRelaxationCommand(relaxation="breathing"))
        assert engine.state == SessionState.SESSION_ENDING

    def test_stray_relaxation_finished_ignored(self, engine, events):
        start(engine)
        engine.process_command(RelaxationFinishedCommand(completed=True))
        assert engine.state == SessionState.CHATTING
        assert not any(isinstance(e, ContinueOrEndAskEvent) for e in events)


class TestTimeLimitDecisions:
    def test_warning_single_shot(self, engine):
        start(engine)
        assert engine.should_emit_time_warning(40.5, 40) is True
        assert engine.should_emit_time_warning(41.0, 40) is False

    def test_warning_not_below_threshold(self, engine):
        start(engine)
        assert engine.should_emit_time_warning(39.0, 40) is False
        assert engine.should_emit_time_warning(40.0, 40) is True

    def test_warning_reset_on_new_session(self, engine):
        start(engine)
        engine.should_emit_time_warning(41.0, 40)
        start(engine)
        assert engine.should_emit_time_warning(41.0, 40) is True

    def test_limit_ask_single_shot(self, engine):
        """Legacy parity: the ask fires once, then stays silent."""
        start(engine)
        assert engine.should_emit_time_limit_ask(44.0, 45) is False
        assert engine.should_emit_time_limit_ask(45.1, 45) is True
        assert engine.should_emit_time_limit_ask(45.2, 45) is False

    def test_continue_choice_suppresses_forever(self, engine):
        start(engine)
        assert engine.should_emit_time_limit_ask(45.1, 45) is True
        engine.acknowledge_time_limit_continue()
        assert engine.should_emit_time_limit_ask(46.0, 45) is False

    def test_limit_ask_reset_on_new_session(self, engine):
        start(engine)
        engine.should_emit_time_limit_ask(45.1, 45)
        start(engine)
        assert engine.should_emit_time_limit_ask(45.1, 45) is True


class TestMarkEnded:
    def test_completes_fsm_and_releases_guard(self, engine, events):
        start(engine)
        engine.process_command(EndSessionCommand(end_type=EndType.QUIT))
        engine.mark_session_ended()
        assert engine.state == SessionState.SESSION_ENDED
        assert not engine.is_ending


class TestThreadedMode:
    def test_submit_processed_on_worker_thread(self, events):
        engine = SessionEngine(emit=events.append)
        engine.start()
        try:
            engine.submit(StartSessionCommand(subject=SubjectInfo(subject_id="S009")))
            deadline = time.time() + 3
            while time.time() < deadline and engine.state != SessionState.CHATTING:
                time.sleep(0.02)
            assert engine.state == SessionState.CHATTING
        finally:
            engine.shutdown()

    def test_shutdown_drains_queue(self, events):
        engine = SessionEngine(emit=events.append)
        engine.start()
        for i in range(5):
            engine.submit(StartSessionCommand(subject=SubjectInfo(subject_id=f"S{i}")))
        engine.shutdown(timeout=3)
        state_changes = [e for e in events if isinstance(e, StateChangedEvent)]
        assert len(state_changes) >= 5

    def test_submit_after_shutdown_dropped(self, events):
        engine = SessionEngine(emit=events.append)
        engine.start()
        engine.shutdown(timeout=2)
        engine.submit(StartSessionCommand(subject=SubjectInfo(subject_id="SZ")))
        time.sleep(0.2)
        assert engine.state == SessionState.IDLE  # never processed


class TestLegacyParityGates:
    def test_ai_relaxation_tag_blocks_force(self, engine, events):
        """Legacy parity: if the AI reply already carried a relaxation tag,
        the end flow must not force another relaxation."""
        start(engine)
        engine.process_command(EndSessionCommand(
            end_type=EndType.GOAL_ACHIEVED, ai_relaxation_tag="呼吸"))
        assert any(isinstance(e, SessionEndingEvent) for e in events)
        assert not any(getattr(e, "kind", "") == "relaxation_recommended" for e in events)

    def test_acknowledge_time_limit_command_suppresses_ask(self, engine):
        start(engine)
        assert engine.should_emit_time_limit_ask(45.1, 45) is True
        engine.process_command(AcknowledgeTimeLimitCommand())
        assert engine.should_emit_time_limit_ask(46.0, 45) is False
