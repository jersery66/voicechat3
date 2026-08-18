"""Transient context for returning from the Relaxation Center."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RelaxationReturnContext:
    """Session-memory-only return hints; no scale answers or scores."""

    source: str
    scale_was_paused: bool
    scale_name: str | None = None
    conversation_anchor: str | None = None
