"""Phase 4 red/green gates for single-owner session lifecycle state."""

from __future__ import annotations

import ast
import threading
from dataclasses import is_dataclass
from pathlib import Path

from app.contracts import (
    CheckTimeLimitCommand,
    MarkSessionEndedCommand,
    PlayGameCommand,
    ScaleProjectionCommand,
    StartSessionCommand,
    SubjectInfo,
)
from app.engine import SessionEngine
from core.session_fsm import SessionState


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _calls(source: str) -> list[ast.Call]:
    return [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)]


def _call_name(node: ast.Call) -> str:
    value = node.func
    parts: list[str] = []
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return ".".join(reversed(parts))


def _stored_attrs(source: str) -> set[str]:
    attrs: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            attrs.add(node.attr)
    return attrs


def test_main_window_has_no_second_lifecycle_owner_or_direct_transitions():
    source = _source("ui/main_window.py")
    names = {_call_name(node) for node in _calls(source)}
    assert "SessionOrchestrator" not in names
    assert "SessionEndController" not in names
    assert "self.orchestrator.transition_to" not in names
    assert "self.orchestrator.evaluate_session_end" not in names
    assert "self.session_end_controller.begin" not in names
    assert source.count("SessionEngine(") == 1


def test_main_window_does_not_store_legacy_lifecycle_flags():
    source = _source("ui/main_window.py")
    forbidden = {
        "_session_ending",
        "_pending_end_after_video",
        "_pending_quit",
        "_user_explicit_end",
        "_scale_interrupted_by_relaxation",
        "_resume_scale_after_relaxation",
        "_post_relaxation_feedback_consumed",
    }
    assert not (_stored_attrs(source) & forbidden)


def test_main_window_reads_lifecycle_from_engine_not_legacy_orchestrator():
    source = _source("ui/main_window.py")
    assert "self.orchestrator" not in source
    assert "SessionState" not in source


def test_pipeline_does_not_store_session_lifecycle_flags():
    source = _source("services/pipeline.py")
    forbidden = {
        "relaxation_active",
        "relaxation_completed",
        "relaxation_used",
        "exit_requested",
        "finish_mode",
    }
    assert not (_stored_attrs(source) & forbidden)


def test_pipeline_and_report_service_do_not_transition_sessions():
    pipeline = _source("services/pipeline.py")
    report = _source("services/report_service.py")
    assert ".transition_to(" not in pipeline
    assert "evaluate_session_end(" not in pipeline
    assert ".transition_to(" not in report
    assert "evaluate_session_end(" not in report


def test_shadow_switches_are_removed_after_authority_migration():
    source = _source("config.py")
    assert "SESSION_ENGINE_SHADOW" not in source
    assert "SESSION_ENGINE_AUTHORITATIVE" not in source


def test_engine_snapshot_is_immutable_and_has_no_scale_shadow_state():
    engine = SessionEngine()
    try:
        engine.process_command(
            StartSessionCommand(subject=SubjectInfo(subject_id="PHASE4-SNAPSHOT"))
        )
        snapshot = engine.snapshot()
        assert is_dataclass(snapshot)
        assert snapshot.session_state == SessionState.CHATTING
        assert snapshot.scale_active is False
        assert not hasattr(snapshot, "active_scale")
        assert not hasattr(snapshot, "current_item")
        assert not hasattr(snapshot, "answers_by_scale")
        assert not hasattr(snapshot, "waiting_for_answer")
        assert not hasattr(snapshot, "completed_scales")
        assert not hasattr(snapshot, "resume_item")
        try:
            snapshot.session_state = SessionState.IDLE
        except Exception:
            pass
        assert snapshot.session_state == SessionState.CHATTING
    finally:
        engine.shutdown()


def test_engine_state_event_is_emitted_by_writer_thread():
    event_threads: list[str] = []
    engine = SessionEngine(
        emit=lambda event: event_threads.append(threading.current_thread().name)
    )
    try:
        engine.start()
        engine.submit(
            StartSessionCommand(subject=SubjectInfo(subject_id="PHASE4-THREAD"))
        )
        assert engine.wait_for_state(SessionState.CHATTING, timeout=2.0)
        assert "session-engine" in event_threads
    finally:
        engine.shutdown()


def test_engine_accepts_lifecycle_projection_without_scale_details():
    engine = SessionEngine()
    try:
        engine.process_command(
            StartSessionCommand(subject=SubjectInfo(subject_id="PHASE4-PROJECTION"))
        )
        engine.process_command(ScaleProjectionCommand(active=True))
        snapshot = engine.snapshot()
        assert snapshot.scale_active is True
        assert not hasattr(snapshot, "active_scale")
        engine.process_command(ScaleProjectionCommand(active=False))
        assert engine.snapshot().scale_active is False
    finally:
        engine.shutdown()


def test_engine_handles_game_and_mark_ended_commands():
    events = []
    engine = SessionEngine(emit=events.append)
    try:
        engine.process_command(
            StartSessionCommand(subject=SubjectInfo(subject_id="PHASE4-CYCLE"))
        )
        engine.process_command(PlayGameCommand())
        assert engine.state is SessionState.VIDEO_PLAYING
        from app.contracts import RelaxationFinishedCommand, EndSessionCommand
        engine.process_command(RelaxationFinishedCommand(completed=True))
        engine.process_command(EndSessionCommand(allow_force_relaxation=False))
        engine.process_command(MarkSessionEndedCommand(farewell_text="再见"))
        assert engine.state is SessionState.SESSION_ENDED
        assert any(getattr(e, "kind", "") == "session_ended" for e in events)
    finally:
        engine.shutdown()


def test_time_limit_markers_are_consumed_by_engine_writer():
    events = []
    engine = SessionEngine(emit=events.append)
    try:
        engine.process_command(
            StartSessionCommand(subject=SubjectInfo(subject_id="PHASE4-TIME"))
        )
        engine.process_command(CheckTimeLimitCommand(
            duration_minutes=40.0, warning_minutes=40.0, max_minutes=45.0
        ))
        assert any(getattr(event, "kind", "") == "session_warning" for event in events)
        engine.process_command(CheckTimeLimitCommand(
            duration_minutes=45.0, warning_minutes=40.0, max_minutes=45.0
        ))
        assert any(getattr(event, "kind", "") == "time_limit_ask" for event in events)
    finally:
        engine.shutdown()
