# app.engine — SessionEngine, the single-writer session facade.
#
# Design principle (the core decision of the Phase-2 refactor):
#   ALL mutable session state transitions happen on ONE thread (the engine
#   worker), fed by a command queue. Every other thread — Qt UI, pipeline,
#   timers — only submits commands and receives events. Shared-mutable-state
#   races disappear by construction, no locks required.
#
# Stage 1 scope (this file):
#   - command/event plumbing (queue + worker loop, or synchronous mode)
#   - session lifecycle decisions built on core FSMs:
#       start_session / end_session (incl. forced-relaxation interception) /
#       play_relaxation / relaxation_finished / continue_chat
#   - time-limit decisions with legacy single-shot semantics
#
# MainWindow still runs the legacy flow as of this commit; the engine is
# wired in incrementally (each handler mirrors a legacy MainWindow path so
# behavior can be diffed 1:1).

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable, Optional

from core.types import EndType
from core.session_fsm import SessionOrchestrator, SessionState
from core.end_guard import SessionEndController
from app.contracts import (
    Command,
    ContinueChatCommand,
    ContinueOrEndAskEvent,
    EndSessionCommand,
    ErrorEvent,
    Event,
    PlayRelaxationCommand,
    RelaxationFinishedCommand,
    RelaxationRecommendedEvent,
    SessionEndingEvent,
    StartSessionCommand,
    StateChangedEvent,
)

logger = logging.getLogger(__name__)

# End types that must NEVER be intercepted by a forced relaxation.
_NO_FORCE_END_TYPES = (EndType.SAFETY, EndType.INVALID, EndType.QUIT)


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
        # end request deferred while a relaxation video is playing
        # (mirrors legacy MainWindow._pending_end_after_video)
        self._pending_end: Optional[EndSessionCommand] = None

        self._handlers = {
            "start_session": self._handle_start_session,
            "end_session": self._handle_end_session,
            "play_relaxation": self._handle_play_relaxation,
            "relaxation_finished": self._handle_relaxation_finished,
            "continue_chat": self._handle_continue_chat,
            "acknowledge_time_limit": self._handle_acknowledge_time_limit,
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
            return
        try:
            handler(command)
        except Exception:
            logger.exception(f"SessionEngine: handler failed for {command.kind!r}")

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
        logger.info(f"SessionEngine: session started for {command.subject.subject_id!r}")
        self._emit_state()

    def _handle_end_session(self, command: EndSessionCommand) -> None:
        """End request. Mirrors legacy MainWindow._handle_session_end:

        1. duplicate end attempts are rejected by the guard;
        2. unless forbidden (explicit exit / SAFETY / INVALID / QUIT, or a
           relaxation video was already played/recommended), the end is
           intercepted by one forced relaxation recommendation;
        3. if the FSM cannot enter SESSION_ENDING right now (a relaxation
           video is playing), the request is DEFERRED until the video ends
           (legacy MainWindow._pending_end_after_video) — the guard is
           released meanwhile so the resumed flow can acquire it;
        4. otherwise the end flow starts (SESSION_ENDING + SessionEndingEvent
           so the client runs farewell/report generation).
        """
        if not self._guard.begin().accepted:
            logger.info("SessionEngine: end request ignored (already ending)")
            return

        force_allowed = (
            command.allow_force_relaxation
            and command.end_type not in _NO_FORCE_END_TYPES
            # legacy gate: if the AI reply already recommended a relaxation,
            # do not force another one
            and not command.ai_relaxation_tag
            # legacy gate: ANY played relaxation (completed or not) blocks
            # the forced recommendation — read the FSM context, not a local flag
            and not self._orchestrator.ctx.current_relaxation_type
            and not self._orchestrator.ctx.has_forced_relaxation_rec
        )

        if force_allowed:
            self._orchestrator.ctx.has_forced_relaxation_rec = True
            self._orchestrator.transition_to(SessionState.RELAXATION_RECOMMENDED)
            self._guard.defer_for_relaxation()
            self._emit_state()
            self._emit(RelaxationRecommendedEvent(
                relaxation=command.relaxation_hint or "breathing",
                forced=True,
            ))
            return

        if self._orchestrator.state == SessionState.VIDEO_PLAYING:
            # Defer until the video finishes; relaxations gate is already
            # satisfied at that point, so the flow proceeds to reports.
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
        self._emit(SessionEndingEvent(end_type=command.end_type))

    def _handle_play_relaxation(self, command: PlayRelaxationCommand) -> None:
        """Relaxation button clicked: enter VIDEO_PLAYING."""
        if not self._orchestrator.can_play_video():
            logger.warning(
                f"SessionEngine: play_relaxation rejected in state {self._orchestrator.state}"
            )
            return
        self._orchestrator.ctx.current_relaxation_type = command.relaxation
        self._orchestrator.transition_to(SessionState.VIDEO_PLAYING)
        self._emit_state()

    def _handle_relaxation_finished(self, command: RelaxationFinishedCommand) -> None:
        """Video ended: move to POST_RELAXATION, then either resume a
        deferred end request or ask continue-or-end."""
        if self._orchestrator.state != SessionState.VIDEO_PLAYING:
            logger.warning(
                f"SessionEngine: relaxation_finished ignored in state "
                f"{self._orchestrator.state.name}"
            )
            return
        self._orchestrator.transition_to(SessionState.POST_RELAXATION)
        self._emit_state()

        if self._pending_end is not None:
            pending = self._pending_end
            self._pending_end = None
            self._handle_end_session(pending)
            return

        self._emit(ContinueOrEndAskEvent(reason="post_relaxation"))

    def _handle_continue_chat(self, command: ContinueChatCommand) -> None:
        """User chose to keep chatting after relaxation."""
        self._orchestrator.transition_to(SessionState.CHATTING)
        self._emit_state()

    def _handle_acknowledge_time_limit(self, command) -> None:
        """User chose 'continue chatting' in the time-limit dialog:
        the ask must never fire again this session (legacy
        continued_after_time_limit parity)."""
        self.acknowledge_time_limit_continue()

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

    def mark_session_ended(self) -> None:
        """Client finished report generation: complete the FSM + release guard."""
        self._orchestrator.transition_to(SessionState.SESSION_ENDED)
        self._guard.reset()
        self._emit_state()
