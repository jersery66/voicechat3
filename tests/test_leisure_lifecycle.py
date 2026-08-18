"""Phase 4.1 leisure lifecycle contracts."""

from __future__ import annotations

import pytest

from app.contracts import (
    EndSessionCommand,
    LeisureFinishedCommand,
    PlayLeisureCommand,
    SessionEndingEvent,
    StartSessionCommand,
    SubjectInfo,
)
from app.engine import SessionEngine
from core.session_fsm import SessionState


@pytest.fixture
def engine():
    events = []
    value = SessionEngine(emit=events.append)
    value._test_events = events
    value.process_command(StartSessionCommand(subject=SubjectInfo(subject_id="LEISURE-1")))
    yield value
    value.shutdown()


def test_play_leisure_enters_active_media_without_core_relaxation_type(engine):
    engine.process_command(PlayLeisureCommand(content_id="bubble_pop"))

    assert engine.state is SessionState.VIDEO_PLAYING
    snapshot = engine.snapshot()
    assert snapshot.playback_kind == "leisure"
    assert snapshot.leisure_content_id == "bubble_pop"
    assert snapshot.relaxation_type is None
    assert engine.can_start_pipeline() is False


def test_normal_leisure_finish_returns_to_chat_without_post_relaxation(engine):
    engine.process_command(PlayLeisureCommand(content_id="gentle_search"))
    engine._test_events.clear()

    engine.process_command(LeisureFinishedCommand(content_id="gentle_search", completed=True))

    assert engine.state is SessionState.CHATTING
    assert engine.snapshot().playback_kind is None
    assert engine.snapshot().leisure_content_id is None
    assert not any(event.kind == "continue_or_end_ask" for event in engine._test_events)


def test_cancelled_leisure_finish_returns_to_chat_without_therapeutic_feedback(engine):
    engine.process_command(PlayLeisureCommand(content_id="falling_leaves"))
    engine.process_command(
        LeisureFinishedCommand(
            content_id="falling_leaves", completed=False, cancelled=True, reason="participant_exit"
        )
    )

    assert engine.state is SessionState.CHATTING
    assert engine.snapshot().playback_kind is None


def test_pending_end_is_deferred_and_resumes_after_leisure_finish(engine):
    engine.process_command(PlayLeisureCommand(content_id="calm_puzzle"))
    engine.process_command(EndSessionCommand())
    assert engine.state is SessionState.VIDEO_PLAYING
    assert engine.snapshot().pending_end is True

    engine.process_command(LeisureFinishedCommand(content_id="calm_puzzle", completed=True))

    assert engine.state is SessionState.SESSION_ENDING
    assert any(isinstance(event, SessionEndingEvent) for event in engine._test_events)


def test_stale_leisure_finish_is_rejected_without_closing_current_game(engine):
    engine.process_command(PlayLeisureCommand(content_id="bubble_pop"))
    engine._test_events.clear()

    engine.process_command(LeisureFinishedCommand(content_id="calm_puzzle", completed=True))

    assert engine.state is SessionState.VIDEO_PLAYING
    assert engine.snapshot().leisure_content_id == "bubble_pop"
    assert any(event.kind == "error" for event in engine._test_events)

def test_leisure_is_rejected_while_scale_projection_is_active(engine):
    from app.contracts import ScaleProjectionCommand

    engine.process_command(ScaleProjectionCommand(active=True))
    engine.process_command(PlayLeisureCommand(content_id="bubble_pop"))

    assert engine.state is SessionState.CHATTING
    assert engine.snapshot().playback_kind is None
    assert any(event.kind == "error" for event in engine._test_events)
