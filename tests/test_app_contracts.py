"""Tests for app.contracts — command/event protocol models."""

import pytest

from core.types import EndType
from app.contracts import (
    AnyCommand,
    AnyEvent,
    ContinueOrEndAskEvent,
    EndSessionCommand,
    ErrorEvent,
    PlayRelaxationCommand,
    RelaxationRecommendedEvent,
    SessionEndedEvent,
    StartSessionCommand,
    StreamDeltaEvent,
    SubjectInfo,
    UserTextCommand,
    RELAXATION_DISPLAY_NAMES,
    parse_command,
    parse_event,
)


class TestCommands:
    def test_start_session_defaults(self):
        cmd = StartSessionCommand(subject=SubjectInfo(subject_id="S001"))
        assert cmd.kind == "start_session"
        assert cmd.subject.age is None

    def test_end_session_flags(self):
        cmd = EndSessionCommand(end_type=EndType.QUIT, allow_force_relaxation=False)
        assert cmd.end_type == EndType.QUIT
        assert cmd.allow_force_relaxation is False

    def test_relaxation_kind_validated(self):
        cmd = PlayRelaxationCommand(relaxation="breathing")
        assert cmd.relaxation == "breathing"
        with pytest.raises(Exception):
            PlayRelaxationCommand(relaxation="not_a_kind")

    def test_commands_are_frozen(self):
        cmd = UserTextCommand(text="你好")
        with pytest.raises(Exception):
            cmd.text = "别的"


class TestEvents:
    def test_stream_delta(self):
        ev = StreamDeltaEvent(text="嗯")
        assert ev.kind == "stream_delta"
        assert ev.ts is not None

    def test_session_ended_carries_paths(self):
        ev = SessionEndedEvent(end_type=EndType.GOAL_ACHIEVED, pdf_path="x.pdf")
        assert ev.pdf_path == "x.pdf"

    def test_continue_or_end_reason_default(self):
        ev = ContinueOrEndAskEvent()
        assert ev.reason == "post_relaxation"


class TestRoundtrip:
    @pytest.mark.parametrize("payload,expected", [
        ({"kind": "user_text", "text": "你好"}, UserTextCommand),
        ({"kind": "end_session"}, EndSessionCommand),
        ({"kind": "play_relaxation", "relaxation": "muscle"}, PlayRelaxationCommand),
    ])
    def test_parse_command(self, payload, expected):
        cmd = parse_command(payload)
        assert isinstance(cmd, expected)

    def test_parse_command_full_roundtrip(self):
        original = StartSessionCommand(
            subject=SubjectInfo(subject_id="S002", age=28, addiction_type="冰毒")
        )
        decoded = parse_command(original.model_dump(mode="json"))
        assert decoded.subject.subject_id == "S002"
        assert decoded.subject.addiction_type == "冰毒"

    def test_parse_event_roundtrip(self):
        original = ErrorEvent(message="TTS failed", recoverable=True, context="tts")
        decoded = parse_event(original.model_dump(mode="json"))
        assert decoded.message == "TTS failed"
        assert decoded.recoverable is True

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            parse_command({"kind": "nope"})
        with pytest.raises(ValueError):
            parse_event({"kind": "nope"})


class TestRegistry:
    def test_event_union_has_no_crisis_alert_kind(self):
        from app.contracts import _EVENT_TYPES

        assert "crisis_alert" not in _EVENT_TYPES

    def test_all_kinds_unique_commands(self):
        kinds = [c.model_fields["kind"].default for c in AnyCommand.__args__]
        assert len(kinds) == len(set(kinds))

    def test_all_kinds_unique_events(self):
        kinds = [e.model_fields["kind"].default for e in AnyEvent.__args__]
        assert len(kinds) == len(set(kinds))

    def test_display_names_cover_all_kinds(self):
        assert set(RELAXATION_DISPLAY_NAMES) == {"breathing", "muscle", "meditation", "game"}
