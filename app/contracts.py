# app.contracts — command/event contracts between client and engine.
#
# Single source of truth for everything that crosses the boundary:
#   - Commands: client (UI) -> engine requests
#   - Events:   engine -> client notifications / stream data
#
# Phase 2 these travel through in-process queues; in Phase 3 the exact
# same models are serialized over WebSocket (each carries a `kind`
# discriminator for JSON routing). Behavior of the running app is NOT
# affected by this module — it is additive until wired in.

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.types import EndType


# ==================== Shared enums / value objects ====================

RelaxationKind = Literal["breathing", "muscle", "meditation", "game"]

# Tag name (core.tags REC_TAGS values) -> normalized kind. REC_TAGS already
# yields exactly these strings, so this is an identity map kept explicit so
# future renames stay in one place.
REC_TAG_TO_KIND: Dict[str, RelaxationKind] = {
    "breathing": "breathing",
    "muscle": "muscle",
    "meditation": "meditation",
    "game": "game",
}

# Engine-facing display names (Chinese) — used in prompts/reports. The three
# legacy mapping tables in report_service/report_generator/main_window will
# converge on this single source during Phase 2 wiring.
RELAXATION_DISPLAY_NAMES: Dict[str, str] = {
    "breathing": "呼吸放松训练",
    "muscle": "肌肉放松训练",
    "meditation": "冥想放松训练",
    "game": "互动小游戏",
}


class SubjectInfo(BaseModel):
    """Participant profile supplied by the left-panel form."""
    model_config = ConfigDict(extra="allow")  # tolerate legacy extra fields

    subject_id: str
    gender: Optional[str] = None
    age: Optional[int] = None
    education: Optional[str] = None
    marital_status: Optional[str] = None
    addiction_type: Optional[str] = None


# ==================== Commands (client -> engine) ====================

class Command(BaseModel):
    """Base class for all client -> engine commands."""
    model_config = ConfigDict(frozen=True)
    kind: str = Field(init=False)


class StartSessionCommand(Command):
    """Begin a new counseling session for the given subject."""
    kind: Literal["start_session"] = "start_session"
    subject: SubjectInfo


class UserTextCommand(Command):
    """User submitted text (keyboard input path)."""
    kind: Literal["user_text"] = "user_text"
    text: str


class StartRecordingCommand(Command):
    """Begin capturing microphone audio."""
    kind: Literal["start_recording"] = "start_recording"


class StopRecordingCommand(Command):
    """Stop capturing; engine runs STT + pipeline on the result."""
    kind: Literal["stop_recording"] = "stop_recording"


class EndSessionCommand(Command):
    """Request session end.

    allow_force_relaxation=False is used for explicit user exits (退出程序),
    matching the legacy `_handle_session_end(..., allow_force_relaxation=...)`.
    """
    kind: Literal["end_session"] = "end_session"
    end_type: EndType = EndType.GOAL_ACHIEVED
    allow_force_relaxation: bool = True
    source: str = ""
    # Relaxation type suggested by the AI reply tags; used only when the end
    # flow forces one last relaxation. None -> engine default ("breathing").
    relaxation_hint: Optional[RelaxationKind] = None


class PlayRelaxationCommand(Command):
    """User clicked one of the relaxation buttons."""
    kind: Literal["play_relaxation"] = "play_relaxation"
    relaxation: RelaxationKind


class RelaxationFinishedCommand(Command):
    """Relaxation video finished (or was exited early)."""
    kind: Literal["relaxation_finished"] = "relaxation_finished"
    completed: bool = True


class ContinueChatCommand(Command):
    """After relaxation, user chose to keep chatting."""
    kind: Literal["continue_chat"] = "continue_chat"


class PlayGameCommand(Command):
    """Launch the therapeutic mini-game (blocks until done in Phase 2)."""
    kind: Literal["play_game"] = "play_game"


class SelectMediaCommand(Command):
    """Entertainment/media scene chosen from the media panel."""
    kind: Literal["select_media"] = "select_media"
    scene: str


class ConfirmUserInfoCommand(Command):
    """Left-panel form confirmed (first time or after modify)."""
    kind: Literal["confirm_user_info"] = "confirm_user_info"
    subject: SubjectInfo


class PrepareNextSubjectCommand(Command):
    """Clean up for the next participant (does not start a session)."""
    kind: Literal["prepare_next_subject"] = "prepare_next_subject"


class ExitCommand(Command):
    """Exit the application (fast quit vs full end handled by engine)."""
    kind: Literal["exit"] = "exit"
    force: bool = False


# Union for dispatch / protocol decoding
AnyCommand = (
    StartSessionCommand
    | UserTextCommand
    | StartRecordingCommand
    | StopRecordingCommand
    | EndSessionCommand
    | PlayRelaxationCommand
    | RelaxationFinishedCommand
    | ContinueChatCommand
    | PlayGameCommand
    | SelectMediaCommand
    | ConfirmUserInfoCommand
    | PrepareNextSubjectCommand
    | ExitCommand
)


# ==================== Events (engine -> client) ====================

class Event(BaseModel):
    """Base class for all engine -> client events."""
    model_config = ConfigDict(frozen=True)
    kind: str = Field(init=False)
    ts: datetime = Field(default_factory=datetime.now)


class StreamDeltaEvent(Event):
    """One streamed chunk of the AI spoken reply."""
    kind: Literal["stream_delta"] = "stream_delta"
    text: str


class AiMessageEvent(Event):
    """A complete AI/user/system message for the chat panel."""
    kind: Literal["ai_message"] = "ai_message"
    role: Literal["ai", "user", "system"]
    text: str


class StateChangedEvent(Event):
    """Session lifecycle state changed (SessionState name)."""
    kind: Literal["state_changed"] = "state_changed"
    state: str


class ScaleProgressEvent(Event):
    """Scale activity update (start / item advance / completed)."""
    kind: Literal["scale_progress"] = "scale_progress"
    action: Literal["started", "advanced", "completed", "interrupted"]
    scale_name: Optional[str] = None
    item: Optional[int] = None
    total_items: Optional[int] = None


class RelaxationRecommendedEvent(Event):
    """Engine recommends a relaxation exercise; UI highlights the button."""
    kind: Literal["relaxation_recommended"] = "relaxation_recommended"
    relaxation: RelaxationKind
    forced: bool = False  # True when the end flow forces one last session


class SessionWarningEvent(Event):
    """Soft warning to surface (e.g. 40-minute time warning)."""
    kind: Literal["session_warning"] = "session_warning"
    message: str


class TimeLimitAskEvent(Event):
    """Hard limit reached: ask continue-or-end."""
    kind: Literal["time_limit_ask"] = "time_limit_ask"


class ContinueOrEndAskEvent(Event):
    """Post-relaxation (or timeout) continue/end choice dialog."""
    kind: Literal["continue_or_end_ask"] = "continue_or_end_ask"
    reason: Literal["post_relaxation", "timeout"] = "post_relaxation"


class CrisisAlertEvent(Event):
    """Crisis risk detected — show hotline dialog."""
    kind: Literal["crisis_alert"] = "crisis_alert"
    risk_level: int
    hotlines: Dict[str, str] = Field(default_factory=dict)


class SessionEndingEvent(Event):
    """End flow accepted: client should run farewell + report generation.

    Emitted instead of SessionEndedEvent at the START of the end flow;
    SessionEndedEvent follows once reports are done.
    """
    kind: Literal["session_ending"] = "session_ending"
    end_type: EndType


class SessionEndedEvent(Event):
    """Session fully ended: reports generated, farewell ready."""
    kind: Literal["session_ended"] = "session_ended"
    end_type: EndType
    farewell_text: str = ""
    report_path: Optional[str] = None
    pdf_path: Optional[str] = None


class ErrorEvent(Event):
    """Something failed; UI decides how to surface it."""
    kind: Literal["error"] = "error"
    message: str
    recoverable: bool = True
    context: str = ""


class StatusEvent(Event):
    """Fine-grained status indicator (listening/thinking/speaking/idle)."""
    kind: Literal["status"] = "status"
    status: Literal["idle", "listening", "thinking", "speaking"]


AnyEvent = (
    StreamDeltaEvent
    | AiMessageEvent
    | StateChangedEvent
    | ScaleProgressEvent
    | RelaxationRecommendedEvent
    | SessionWarningEvent
    | TimeLimitAskEvent
    | ContinueOrEndAskEvent
    | CrisisAlertEvent
    | SessionEndingEvent
    | SessionEndedEvent
    | ErrorEvent
    | StatusEvent
)


# ==================== (De)serialization helpers for Phase 3 ====================

_COMMAND_TYPES = {c.model_fields["kind"].default: c for c in AnyCommand.__args__}
_EVENT_TYPES = {e.model_fields["kind"].default: e for e in AnyEvent.__args__}


def parse_command(payload: Dict[str, Any]) -> Command:
    """Decode a JSON dict into the right Command subclass (WebSocket layer)."""
    kind = payload.get("kind")
    cls = _COMMAND_TYPES.get(kind)
    if cls is None:
        raise ValueError(f"unknown command kind: {kind!r}")
    return cls.model_validate(payload)


def parse_event(payload: Dict[str, Any]) -> Event:
    """Decode a JSON dict into the right Event subclass (WebSocket layer)."""
    kind = payload.get("kind")
    cls = _EVENT_TYPES.get(kind)
    if cls is None:
        raise ValueError(f"unknown event kind: {kind!r}")
    return cls.model_validate(payload)
