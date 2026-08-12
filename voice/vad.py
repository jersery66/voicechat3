"""Voice-activity detector contract."""

from typing import Protocol, runtime_checkable

from voice.contracts import AudioFrame


@runtime_checkable
class VoiceActivityDetector(Protocol):
    def is_speech(self, frame: AudioFrame) -> bool: ...
