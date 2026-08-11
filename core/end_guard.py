# core.end_guard — session-end guard state (pure logic).
#
# Extracted from services/session_end_controller.py without behavior change.
# services/session_end_controller.py re-exports every name for compatibility.

import threading
from dataclasses import dataclass


@dataclass
class EndGuardResult:
    """Result of attempting to enter session-ending flow."""

    accepted: bool
    reason: str = ""


class SessionEndController:
    """Guards against duplicate report generation and supports relaxation deferral."""

    def __init__(self) -> None:
        self._ending = False
        # Protects the check-and-set in begin() and the clear in
        # defer_for_relaxation() so they are atomic under concurrent callers.
        self._lock = threading.Lock()

    @property
    def is_ending(self) -> bool:
        with self._lock:
            return self._ending

    def begin(self) -> EndGuardResult:
        """Enter ending flow unless already active (atomic check + set)."""
        with self._lock:
            if self._ending:
                return EndGuardResult(False, "already_ending")
            self._ending = True
        return EndGuardResult(True)

    def defer_for_relaxation(self) -> None:
        """Release the guard when ending is intercepted by a relaxation step.

        Only clears the flag when we are actually in the ending flow; a stray
        call outside of an active end must not wipe a legitimate guard state.
        """
        with self._lock:
            if self._ending:
                self._ending = False

    def reset(self) -> None:
        """Reset guard for a new session."""
        with self._lock:
            self._ending = False
