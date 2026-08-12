"""Live audio source contract."""

from typing import Protocol, runtime_checkable

from voice.contracts import AudioFrame


@runtime_checkable
class AudioInput(Protocol):
    def read_frame(self) -> AudioFrame: ...
