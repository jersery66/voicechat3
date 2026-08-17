"""SessionEngine lifecycle torture checks using the real single writer."""

from __future__ import annotations

from app.contracts import (
    EndSessionCommand,
    MarkSessionEndedCommand,
    PrepareNextSubjectCommand,
    StartSessionCommand,
    SubjectInfo,
)
from app.engine import SessionEngine
from core.session_fsm import SessionState
from core.types import EndType


def _started_engine(subject="SYNTH-001"):
    events = []
    engine = SessionEngine(emit=events.append)
    engine.process_command(StartSessionCommand(subject=SubjectInfo(subject_id=subject)))
    return engine, events


def test_new_session_starts_clean_and_prepare_returns_to_idle():
    engine, _events = _started_engine()
    assert engine.snapshot().session_state is SessionState.CHATTING
    engine.process_command(PrepareNextSubjectCommand())
    assert engine.snapshot().session_state is SessionState.IDLE


def test_end_report_mark_sequence_is_owned_by_session_engine():
    engine, _events = _started_engine()
    engine.process_command(EndSessionCommand(end_type=EndType.GOAL_ACHIEVED))
    assert engine.snapshot().session_state is SessionState.SESSION_ENDING
    engine.process_command(MarkSessionEndedCommand(report_path="synthetic-report.json"))
    assert engine.snapshot().session_state is SessionState.SESSION_ENDED


def test_repeated_start_end_cycles_do_not_leave_terminal_state_in_next_session():
    engine, _events = _started_engine("SYNTH-A")
    for index in range(3):
        engine.process_command(EndSessionCommand(end_type=EndType.GOAL_ACHIEVED))
        engine.process_command(MarkSessionEndedCommand(report_path=f"report-{index}.json"))
        engine.process_command(PrepareNextSubjectCommand())
        engine.process_command(StartSessionCommand(subject=SubjectInfo(subject_id=f"SYNTH-{index + 2}")))
        assert engine.snapshot().session_state is SessionState.CHATTING


def test_engine_does_not_accept_user_or_scale_commands_as_lifecycle_writes():
    engine, events = _started_engine()
    from app.contracts import ScaleProjectionCommand, UserTextCommand

    engine.process_command(UserTextCommand(text="synthetic"))
    engine.process_command(ScaleProjectionCommand(active=True))
    # Projection is the only permitted session-level view; it does not carry
    # item/answer truth or turn policy decisions.
    assert engine.snapshot().scale_active is True
    assert not any(getattr(event, "state", None) == "SESSION_ENDING" for event in events)
