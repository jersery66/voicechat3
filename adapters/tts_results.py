"""Canonical result contract for text-to-speech generation and playback."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PlaybackStatus(str, Enum):
    """The only terminal outcomes of a TTS playback request."""

    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class PlaybackResult:
    """Explicit playback outcome with an optional internal error reason."""

    status: PlaybackStatus
    error: str = ""

    @property
    def ok(self) -> bool:
        """Compatibility convenience; only a completed playback is successful."""

        return self.status is PlaybackStatus.COMPLETED


__all__ = ["PlaybackResult", "PlaybackStatus"]
