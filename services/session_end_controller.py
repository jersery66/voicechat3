"""Small controller for session-end guard state.

COMPATIBILITY SHIM: the implementation moved to core.end_guard as part of
the Phase-1 modularization. All names are re-exported so existing imports
(`from services.session_end_controller import SessionEndController`) keep
working unchanged.

The UI owns dialogs, TTS, and report threads; this object owns the tiny but
important "are we already ending?" state so the lock can be unit-tested away
from Qt.
"""

from core.end_guard import (  # noqa: F401  (re-exported for backward compatibility)
    EndGuardResult,
    SessionEndController,
)
