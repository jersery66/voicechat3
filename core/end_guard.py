# core.end_guard — session-end guard state (pure logic).
#
# Extracted from services/session_end_controller.py without behavior change.
# services/session_end_controller.py re-exports every name for compatibility.

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

    @property
    def is_ending(self) -> bool:
        return self._ending

    def begin(self) -> EndGuardResult:
        """Enter ending flow unless already active."""
        if self._ending:
            return EndGuardResult(False, "already_ending")
        self._ending = True
        return EndGuardResult(True)

    def defer_for_relaxation(self) -> None:
        """Release the guard when ending is intercepted by a relaxation step."""
        self._ending = False

    def reset(self) -> None:
        """Reset guard for a new session."""
        self._ending = False
