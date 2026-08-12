"""Incremental TTS contract."""

from typing import Iterator, Protocol, runtime_checkable

from voice.contracts import AudioFrame


@runtime_checkable
class StreamingTTS(Protocol):
    def stream(self, text: str) -> Iterator[AudioFrame]: ...
