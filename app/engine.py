# app.engine — SessionEngine, the single-writer session facade.
#
# Design principle (the core decision of the Phase-2 refactor):
#   ALL mutable session state transitions happen on ONE thread (the engine
#   worker), fed by a command queue. Every other thread — Qt UI, pipeline,
#   timers — only submits commands and receives events. Shared-mutable-state
#   races disappear by construction, no locks required.
#
# Lifecycle ownership scope (this file):
#   - command/event plumbing (queue + worker loop, or synchronous mode)
#   - session lifecycle decisions built on core FSMs:
#       start_session / end_session (incl. forced-relaxation interception) /
#       play_relaxation / relaxation_finished / continue_chat
#   - time-limit decisions with legacy single-shot semantics
#
# MainWindow submits commands and renders the events emitted by this engine;
# it does not own a second lifecycle state machine.

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from core.types import EndType
from core.session_fsm import SessionContext, SessionOrchestrator, SessionState
from core.end_guard import SessionEndController
from app.contracts import (
    Command,
    ContinueChatCommand,
    ContinueOrEndAskEvent,
    EndSessionCommand,
    ErrorEvent,
    Event,
    ExitCommand,
    CheckTimeLimitCommand,
    PlayRelaxationCommand,
    PlayGameCommand,
    RelaxationFinishedCommand,
    PlayLeisureCommand,
    LeisureFinishedCommand,
    LeisureStartedEvent,
    SessionEndedEvent,
    SessionEndingEvent,
    StartSessionCommand,
    StateChangedEvent,
    SessionWarningEvent,
    TimeLimitAskEvent,
    TimeLimitAcknowledgedEvent,
    PrepareNextSubjectCommand,
    ScaleProjectionCommand,
    MarkSessionEndedCommand,
)

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class SessionLifecycleSnapshot:
    """Immutable lifecycle read model exposed by :class:`SessionEngine`.

    The snapshot deliberately contains only session-level facts.  Questionnaire
    state remains in ``assessment.ScaleRuntimeSnapshot`` and is never copied
    into this object.
    """

    session_state: SessionState
    pending_end: bool = False
    pending_end_type: Optional[EndType] = None
    relaxation_type: Optional[str] = None
    playback_kind: Optional[str] = None
    leisure_content_id: Optional[str] = None
    time_warning_sent: bool = False
    time_limit_ask_sent: bool = False
    time_limit_continue_chosen: bool = False
    is_ending: bool = False
    terminal: bool = False
    scale_active: bool = False
    exit_requested: bool = False


class SessionEngine:
    """Single-writer facade owning session lifecycle state.

    Usage (production, threaded):
        engine = SessionEngine(emit=ui_bridge.on_event)
        engine.start()
        engine.submit(StartSessionCommand(subject=...))
        ...
        engine.shutdown()

    Usage (tests / synchronous):
        engine = SessionEngine()
        engine.process_command(cmd)   # runs the handler inline

    Note: submit() after shutdown() is rejected (logged and dropped); the
    worker is a daemon thread, so any commands still queued at interpreter
    exit are discarded.
    """

    def __init__(self, emit: Optional[Callable[[Event], None]] = None) -> None:
        self._emit_cb = emit or (lambda _event: None)
        self._orchestrator = SessionOrchestrator()
        self._guard = SessionEndController()
        self._queue: "queue.Queue[Command]" = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # decision bookkeeping (owned by the writer thread only)
        self._time_warning_sent = False
        self._time_limit_ask_sent = False
        self._time_limit_continue_chosen = False
        # End request deferred while playback is active.  This record is
        # mutated only by the engine writer and is exposed read-only in the
        # snapshot.
        self._pending_end: Optional[EndSessionCommand] = None
        # end_type of the most recent accepted end flow, surfaced again in
        # SessionEndedEvent (H15).
        self._last_end_type: Optional[EndType] = None
        # Session-level projection only.  ScaleRuntime remains the source of
        # item/answer/waiting/pause/completion state.
        self._scale_active = False
        self._playback_kind: Optional[str] = None
        self._leisure_content_id: Optional[str] = None
        self._exit_requested = False

        self._handlers = {
            "start_session": self._handle_start_session,
            "end_session": self._handle_end_session,
            "play_relaxation": self._handle_play_relaxation,
            "relaxation_finished": self._handle_relaxation_finished,
            "play_leisure": self._handle_play_leisure,
            "leisure_finished": self._handle_leisure_finished,
            "continue_chat": self._handle_continue_chat,
            "acknowledge_time_limit": self._handle_acknowledge_time_limit,
            "check_time_limit": self._handle_check_time_limit,
            "play_game": self._handle_play_game,
            "prepare_next_subject": self._handle_prepare_next_subject,
            "scale_projection": self._handle_scale_projection,
            "mark_session_ended": self._handle_mark_session_ended,
            "exit": self._handle_exit,
            # Commands that are intentionally outside lifecycle ownership are
            # explicitly rejected so nothing is silently dropped.
            "user_text": self._handle_unimplemented,
            "start_recording": self._handle_unimplemented,
            "stop_recording": self._handle_unimplemented,
            "select_media": self._handle_unimplemented,
            "confirm_user_info": self._handle_unimplemented,
        }

    # ==================== lifecycle ====================

    def start(self) -> None:
        """Start the worker (writer) thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="session-engine", daemon=True
        )
        self._thread.start()

    def shutdown(self, timeout: float = 2.0) -> None:
        """Stop the worker thread after draining pending commands.

        If the worker does not stop within timeout the thread reference is
        kept so a later start() cannot create a second writer (the
        single-writer invariant must never break silently).
        """
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            if not self._thread.is_alive():
                self._thread = None
            else:
                logger.warning("SessionEngine: worker did not stop on shutdown")

    def submit(self, command: Command) -> None:
        """Thread-safe entry point: enqueue a command for the writer loop."""
        if self._stop_event.is_set():
            logger.warning(
                f"SessionEngine: submit after shutdown dropped ({command.kind!r})"
            )
            return
        self._queue.put(command)

    def process_command(self, command: Command) -> None:
        """Run one command handler inline (writer-thread body / tests)."""
        handler = self._handlers.get(command.kind)
        if handler is None:
            logger.warning(f"SessionEngine: unhandled command kind {command.kind!r}")
            self._emit(ErrorEvent(
                message=f"unhandled command kind: {command.kind!r}",
                recoverable=True,
                context="process_command",
            ))
            return
        try:
            handler(command)
        except Exception:
            logger.exception(f"SessionEngine: handler failed for {command.kind!r}")

    def _handle_unimplemented(self, command: Command) -> None:
        """Explicitly reject commands outside lifecycle ownership.

        H14: previously such commands were silently dropped. Now we emit an
        ErrorEvent so the client (and tests) can see the engine does not own
        this flow yet, rather than wondering why nothing happened.
        """
        logger.info(f"SessionEngine: command {command.kind!r} not implemented by lifecycle engine")
        self._emit(ErrorEvent(
            message=f"command {command.kind!r} is not implemented in this engine build",
            recoverable=True,
            context=command.kind,
        ))

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                command = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            self.process_command(command)
        # drain remaining commands so nothing is silently lost on shutdown
        while True:
            try:
                command = self._queue.get_nowait()
            except queue.Empty:
                break
            self.process_command(command)

    # ==================== state queries (read-only) ====================

    @property
    def state(self) -> SessionState:
        return self._orchestrator.state

    def snapshot(self) -> SessionLifecycleSnapshot:
        """Return an immutable session lifecycle read model."""
        context = self._orchestrator.ctx
        return SessionLifecycleSnapshot(
            session_state=self._orchestrator.state,
            pending_end=self._pending_end is not None,
            pending_end_type=(
                self._pending_end.end_type if self._pending_end is not None else None
            ),
            relaxation_type=context.current_relaxation_type,
            playback_kind=self._playback_kind,
            leisure_content_id=self._leisure_content_id,
            time_warning_sent=self._time_warning_sent,
            time_limit_ask_sent=self._time_limit_ask_sent,
            time_limit_continue_chosen=self._time_limit_continue_chosen,
            is_ending=self._guard.is_ending,
            terminal=self._orchestrator.state is SessionState.SESSION_ENDED,
            scale_active=self._scale_active,
            exit_requested=self._exit_requested,
        )

    def wait_for_state(self, state: SessionState, timeout: float = 2.0) -> bool:
        """Wait for the writer thread to publish ``state`` (test/UI bridge)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.state is state:
                return True
            time.sleep(0.01)
        return self.state is state

    @property
    def is_ending(self) -> bool:
        return self._guard.is_ending

    def can_start_pipeline(self) -> bool:
        return self._orchestrator.can_start_pipeline()

    def can_play_video(self) -> bool:
        return self._orchestrator.can_play_video()

    # ==================== event emission ====================

    def _emit(self, event: Event) -> None:
        try:
            self._emit_cb(event)
        except Exception:
            logger.exception("SessionEngine: emit callback failed")

    def _emit_state(self) -> None:
        self._emit(StateChangedEvent(state=self._orchestrator.state.name))

    # ==================== command handlers (writer thread only) ====================

    def _handle_start_session(self, command: StartSessionCommand) -> None:
        """Fresh session: reset FSMs and enter CHATTING."""
        self._orchestrator.reset()          # -> CHATTING by legacy semantics
        self._guard.reset()
        self._time_warning_sent = False
        self._time_limit_ask_sent = False
        self._time_limit_continue_chosen = False
        self._pending_end = None
        self._last_end_type = None
        self._scale_active = False
        self._playback_kind = None
        self._leisure_content_id = None
        self._exit_requested = False
        logger.info(f"SessionEngine: session started for {command.subject.subject_id!r}")
        self._emit_state()

    def _handle_end_session(self, command: EndSessionCommand) -> None:
        """Execute an already-approved end command.

        Eligibility and intervention policy are decided before this boundary.
        ``allow_force_relaxation`` is retained only as a wire-compatible
        legacy field; this writer never turns it into a recommendation.  The
        engine only validates lifecycle state and defers while media is active.
        """
        if not self._guard.begin().accepted:
            logger.info("SessionEngine: end request ignored (already ending)")
            self._emit(ErrorEvent(
                message="session end request ignored because an end flow is already active",
                recoverable=True,
                context="end_session.duplicate",
            ))
            return

        self._exit_requested = command.end_type is EndType.QUIT

        if self._orchestrator.state == SessionState.VIDEO_PLAYING:
            # Defer until active media finishes; no new intervention policy is
            # invented at this lifecycle boundary.
            self._pending_end = command
            self._guard.defer_for_relaxation()
            logger.info("SessionEngine: end deferred until relaxation video ends")
            return

        if not self._orchestrator.transition_to(SessionState.SESSION_ENDING):
            # Unexpected state — do not pretend the end flow started.
            self._guard.reset()
            self._emit(ErrorEvent(
                message=f"cannot end session from state {self._orchestrator.state.name}",
                recoverable=True,
                context="end_session",
            ))
            return

        self._emit_state()
        self._last_end_type = command.end_type
        self._emit(SessionEndingEvent(end_type=command.end_type))

    def _handle_play_relaxation(self, command: PlayRelaxationCommand) -> None:
        """Relaxation button clicked: enter VIDEO_PLAYING."""
        if not self._orchestrator.can_play_video():
            logger.warning(
                f"SessionEngine: play_relaxation rejected in state {self._orchestrator.state}"
            )
            self._emit(ErrorEvent(
                message=f"cannot play media from state {self._orchestrator.state.name}",
                recoverable=True,
                context="play_relaxation",
            ))
            return
        self._orchestrator.ctx.current_relaxation_type = command.relaxation
        self._playback_kind = "game" if command.relaxation == "game" else "relaxation"
        self._orchestrator.transition_to(SessionState.VIDEO_PLAYING)
        self._emit_state()

    def _handle_play_leisure(self, command: PlayLeisureCommand) -> None:
        """Enter active-media state for a catalog-owned leisure activity."""
        if self._scale_active:
            self._emit(ErrorEvent(
                message="cannot play leisure while a scale is active",
                recoverable=True,
                context="play_leisure.scale_active",
            ))
            return
        if not self._orchestrator.can_play_video():
            logger.warning(
                "SessionEngine: play_leisure rejected in state %s",
                self._orchestrator.state,
            )
            self._emit(ErrorEvent(
                message=f"cannot play leisure from state {self._orchestrator.state.name}",
                recoverable=True,
                context="play_leisure",
            ))
            return
        if not self._orchestrator.transition_to(SessionState.VIDEO_PLAYING):
            self._emit(ErrorEvent(
                message="cannot enter active leisure playback",
                recoverable=True,
                context="play_leisure",
            ))
            return
        # Leisure is active participant-facing content, but it is not a core
        # relaxation intervention and must never populate current_relaxation_type.
        self._playback_kind = "leisure"
        self._leisure_content_id = command.content_id
        self._emit_state()
        self._emit(LeisureStartedEvent(content_id=command.content_id))

    def _handle_relaxation_finished(self, command: RelaxationFinishedCommand) -> None:
        """Video ended: move to POST_RELAXATION, then either resume a
        deferred end request or ask continue-or-end."""
        if (
            self._orchestrator.state != SessionState.VIDEO_PLAYING
            or self._playback_kind == "leisure"
        ):
            logger.warning(
                f"SessionEngine: relaxation_finished ignored in state "
                f"{self._orchestrator.state.name}"
            )
            self._emit(ErrorEvent(
                message=f"relaxation_finished is invalid in state {self._orchestrator.state.name}",
                recoverable=True,
                context="relaxation_finished",
            ))
            return
        if command.provider_failed:
            # A provider failure releases the active-media slot without
            # presenting a successful core-relaxation feedback choice.
            self._orchestrator.transition_to(SessionState.CHATTING)
            self._playback_kind = None
            self._emit_state()
            return
        self._orchestrator.transition_to(SessionState.POST_RELAXATION)
        self._playback_kind = None
        self._emit_state()

        if self._pending_end is not None:
            pending = self._pending_end
            self._pending_end = None
            self._handle_end_session(pending)
            return

        self._emit(ContinueOrEndAskEvent(reason="post_relaxation"))

    def _handle_leisure_finished(self, command: LeisureFinishedCommand) -> None:
        """Close one active leisure run without entering POST_RELAXATION."""
        if (
            self._orchestrator.state is not SessionState.VIDEO_PLAYING
            or self._playback_kind != "leisure"
            or self._leisure_content_id != command.content_id
        ):
            self._emit(ErrorEvent(
                message="leisure_finished is invalid for the current active content",
                recoverable=True,
                context="leisure_finished",
            ))
            return

        self._playback_kind = None
        self._leisure_content_id = None
        pending = self._pending_end
        self._pending_end = None
        if not self._orchestrator.transition_to(SessionState.CHATTING):
            self._emit(ErrorEvent(
                message="cannot return to chatting after leisure content",
                recoverable=True,
                context="leisure_finished",
            ))
            return
        self._emit_state()
        if pending is not None:
            self._handle_end_session(pending)

    def _handle_continue_chat(self, command: ContinueChatCommand) -> None:
        """User chose to keep chatting after relaxation."""
        if not self._orchestrator.transition_to(SessionState.CHATTING):
            self._emit(ErrorEvent(
                message=f"cannot continue chat from state {self._orchestrator.state.name}",
                recoverable=True,
                context="continue_chat",
            ))
            return
        self._emit_state()

    def _handle_play_game(self, command: PlayGameCommand) -> None:
        """Legacy compatibility path for the old therapeutic game service."""
        self._handle_play_relaxation(PlayRelaxationCommand(relaxation="game"))

    def _handle_exit(self, command: ExitCommand) -> None:
        """Translate an application exit into the no-force end contract."""
        self._handle_end_session(EndSessionCommand(
            end_type=EndType.QUIT,
            allow_force_relaxation=False,
            source="exit_command",
        ))

    def _handle_prepare_next_subject(self, command: PrepareNextSubjectCommand) -> None:
        """Return to IDLE without starting the next participant session."""
        self._orchestrator.ctx = SessionContext()
        self._guard.reset()
        self._time_warning_sent = False
        self._time_limit_ask_sent = False
        self._time_limit_continue_chosen = False
        self._pending_end = None
        self._last_end_type = None
        self._scale_active = False
        self._playback_kind = None
        self._leisure_content_id = None
        self._exit_requested = False
        self._emit_state()

    def _handle_scale_projection(self, command: ScaleProjectionCommand) -> None:
        """Record only the boolean activity projection from ScaleRuntime."""
        self._scale_active = bool(command.active)

    def _handle_mark_session_ended(self, command: MarkSessionEndedCommand) -> None:
        """Complete the lifecycle after the client finishes report work."""
        if self._orchestrator.state is not SessionState.SESSION_ENDING:
            self._emit(ErrorEvent(
                message=f"cannot mark session ended from state {self._orchestrator.state.name}",
                recoverable=True,
                context="mark_session_ended",
            ))
            return
        self._orchestrator.transition_to(SessionState.SESSION_ENDED)
        self._guard.reset()
        self._scale_active = False
        self._pending_end = None
        self._leisure_content_id = None
        self._emit_state()
        self._emit(SessionEndedEvent(
            end_type=self._last_end_type or EndType.GOAL_ACHIEVED,
            farewell_text=command.farewell_text,
            report_path=command.report_path,
            pdf_path=command.pdf_path,
        ))

    def _handle_acknowledge_time_limit(self, command) -> None:
        """User chose 'continue chatting' in the time-limit dialog:
        the ask must never fire again this session (legacy
        continued_after_time_limit parity)."""
        self.acknowledge_time_limit_continue()
        self._emit(TimeLimitAcknowledgedEvent())

    def _handle_check_time_limit(self, command: CheckTimeLimitCommand) -> None:
        """Consume warning/limit markers on the lifecycle writer thread."""
        if command.duration_minutes >= command.max_minutes and self.should_emit_time_limit_ask(
            command.duration_minutes, command.max_minutes
        ):
            self._emit(TimeLimitAskEvent())
            return
        if self.should_emit_time_warning(command.duration_minutes, command.warning_minutes):
            remaining = max(0, int(command.max_minutes - command.duration_minutes))
            self._emit(SessionWarningEvent(
                message=(
                    f"我们的对话已进行约{int(command.duration_minutes)}分钟，"
                    f"还剩约{remaining}分钟。"
                )
            ))
            return

    # ==================== time-limit decisions (legacy single-shot) ====================

    def should_emit_time_warning(self, duration_minutes: float,
                                 warning_minutes: float) -> bool:
        """40-minute soft warning — emitted AT MOST once per session."""
        if duration_minutes >= warning_minutes and not self._time_warning_sent:
            self._time_warning_sent = True
            return True
        return False

    def should_emit_time_limit_ask(self, duration_minutes: float,
                                   max_minutes: float) -> bool:
        """45-minute hard limit — the ask fires AT MOST once per session.

        Matches legacy report_service semantics (time_limit_prompt_shown):
        after the first ask, repeated calls return False until the session
        resets. If the user chooses to continue, call
        acknowledge_time_limit_continue() so the ask never returns
        (legacy continued_after_time_limit).
        """
        if duration_minutes < max_minutes:
            return False
        if self._time_limit_ask_sent or self._time_limit_continue_chosen:
            return False
        self._time_limit_ask_sent = True
        return True

    def acknowledge_time_limit_continue(self) -> None:
        """User chose 'continue chatting' in the time-limit dialog."""
        self._time_limit_continue_chosen = True

    def mark_session_ended(self, report_path: Optional[str] = None,
                           farewell_text: str = "", pdf_path: Optional[str] = None) -> None:
        """Client finished report generation: complete the FSM + release guard.

        H15: emits the terminal SessionEndedEvent (with report_path /
        farewell_text) so clients waiting on the session-end contract can
        react. The end_type comes from the SessionEndingEvent emitted earlier.
        """
        self.process_command(MarkSessionEndedCommand(
            report_path=report_path,
            farewell_text=farewell_text,
            pdf_path=pdf_path,
        ))
