"""Tests for app.engine — SessionEngine single-writer facade."""

import time

import pytest

from core.types import EndType
from core.session_fsm import SessionState
from app.contracts import (
    ContinueChatCommand,
    ContinueOrEndAskEvent,
    EndSessionCommand,
    PlayRelaxationCommand,
    RelaxationFinishedCommand,
    RelaxationRecommendedEvent,
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
    def test_goal_achieved_forces_relaxation(self, engine, events):
        start(engine)
        engine.process_command(EndSessionCommand(end_type=EndType.GOAL_ACHIEVED))
        recs = [e for e in events if isinstance(e, RelaxationRecommendedEvent)]
        assert len(recs) == 1
        assert recs[0].forced is True
        assert recs[0].relaxation == "breathing"
        assert engine.state == SessionState.RELAXATION_RECOMMENDED
        # guard was released so the post-relaxation end can proceed
        assert not engine.is_ending

    def test_relaxation_hint_respected(self, engine, events):
        start(engine)
        engine.process_command(EndSessionCommand(
            end_type=EndType.GOAL_ACHIEVED, relaxation_hint="meditation"))
        recs = [e for e in events if isinstance(e, RelaxationRecommendedEvent)]
        assert recs[0].relaxation == "meditation"

    def test_second_end_proceeds_to_reports(self, engine, events):
        start(engine)
        engine.process_command(EndSessionCommand(end_type=EndType.GOAL_ACHIEVED))
        events.clear()
        engine.process_command(EndSessionCommand(end_type=EndType.GOAL_ACHIEVED))
        assert any(isinstance(e, SessionEndingEvent) for e in events)
        assert engine.state == SessionState.SESSION_ENDING

    @pytest.mark.parametrize("kwargs", [
        {"end_type": EndType.QUIT},
        {"end_type": EndType.SAFETY},
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

    def test_completed_relaxation_blocks_force(self, engine, events):
        start(engine)
        engine.process_command(PlayRelaxationCommand(relaxation="muscle"))
        engine.process_command(RelaxationFinishedCommand(completed=True))
        events.clear()
        engine.process_command(EndSessionCommand(end_type=EndType.GOAL_ACHIEVED))
        assert any(isinstance(e, SessionEndingEvent) for e in events)


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


class TestTimeLimitDecisions:
    def test_warning_single_shot(self, engine):
        start(engine)
        assert engine.should_emit_time_warning(40.5, 40) is True
        assert engine.should_emit_time_warning(41.0, 40) is False

    def test_warning_not_below_threshold(self, engine):
        start(engine)
        assert engine.should_emit_time_warning(39.0, 40) is False
        assert engine.should_emit_time_warning(40.0, 40) is True

    def test_limit_ask_persistent(self, engine):
        start(engine)
        assert engine.should_emit_time_limit_ask(44.0, 45) is False
        assert engine.should_emit_time_limit_ask(45.1, 45) is True

    def test_warning_reset_on_new_session(self, engine):
        start(engine)
        engine.should_emit_time_warning(41.0, 40)
        start(engine)
        assert engine.should_emit_time_warning(41.0, 40) is True


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
