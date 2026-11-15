"""Future Adaptive Support Activities contracts and deterministic runtime.

Phase A is deliberately standalone.  It is not imported by the current
ConversationPipeline, TurnPolicy, UI, or SessionEngine.
"""

from .catalog import ActivityCatalog, build_default_catalog
from .contracts import (
    ActivityCandidate,
    ActivityDefinition,
    ActivityResult,
    ActivityRuntimeError,
    ActivityRuntimeSnapshot,
    ActivityStatus,
)
from .runtime import ActivityRuntime

__all__ = [
    "ActivityCandidate",
    "ActivityCatalog",
    "ActivityDefinition",
    "ActivityResult",
    "ActivityRuntime",
    "ActivityRuntimeError",
    "ActivityRuntimeSnapshot",
    "ActivityStatus",
    "build_default_catalog",
]
