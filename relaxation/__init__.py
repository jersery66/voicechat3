"""Relaxation Center V1 foundation.

Phase 1 is intentionally standalone: catalog metadata and a small deterministic
content lifecycle, with no application/UI integration yet.
"""

from .catalog import RelaxationCatalog, build_default_catalog
from .contracts import (
    RelaxationContentDefinition,
    RelaxationContentType,
    RelaxationRuntimeError,
    RelaxationSnapshot,
    RelaxationState,
)
from .runtime import RelaxationRuntime

__all__ = [
    "RelaxationCatalog",
    "RelaxationContentDefinition",
    "RelaxationContentType",
    "RelaxationRuntime",
    "RelaxationRuntimeError",
    "RelaxationSnapshot",
    "RelaxationState",
    "build_default_catalog",
]
