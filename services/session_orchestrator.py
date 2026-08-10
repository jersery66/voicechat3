# Session Orchestrator - State machine for session lifecycle management
#
# COMPATIBILITY SHIM: the implementation moved to core.session_fsm as part of
# the Phase-1 modularization. All names are re-exported so existing imports
# (`from services.session_orchestrator import SessionOrchestrator, ...`)
# keep working unchanged.

from core.session_fsm import (  # noqa: F401  (re-exported for backward compatibility)
    SessionState,
    SessionContext,
    SessionOrchestrator,
)
