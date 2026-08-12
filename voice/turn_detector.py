"""Minimal deterministic end-of-turn detector for streaming voice adapters."""

from __future__ import annotations


class SilenceTurnDetector:
    """Emit one finalization signal after continuous post-speech silence."""

    def __init__(self, *, silence_seconds: float = 1.2):
        self.silence_seconds = silence_seconds
        self._speech_seen = False
        self._silence_started_at: float | None = None
        self._finalized = False

    def feed(self, *, is_speech: bool, timestamp: float) -> bool:
        if is_speech:
            self._speech_seen = True
            self._silence_started_at = None
            self._finalized = False
            return False
        if not self._speech_seen or self._finalized:
            return False
        if self._silence_started_at is None:
            self._silence_started_at = timestamp
            return False
        if timestamp - self._silence_started_at >= self.silence_seconds:
            self._finalized = True
            return True
        return False
