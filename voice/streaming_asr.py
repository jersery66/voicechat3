"""Incremental ASR contract."""

from typing import Protocol, runtime_checkable

from voice.contracts import AudioFrame, Transcription


@runtime_checkable
class StreamingASR(Protocol):
    def transcribe(self, frame: AudioFrame) -> Transcription: ...
