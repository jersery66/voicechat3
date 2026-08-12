"""Data contracts shared by future VAD, ASR, turn detection, and TTS adapters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AudioFrame:
    samples: bytes
    sample_rate: int
    channels: int = 1


@dataclass(frozen=True)
class Transcription:
    text: str
    is_final: bool
    confidence: float | None = None
