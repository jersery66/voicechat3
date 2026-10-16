"""Provider-neutral speech input/output contracts.

A speech provider is chosen by runtime configuration
(``config/runtime.yaml`` -> ``asr.provider`` / ``tts.provider``). The
application layer depends on these protocols only: it never imports a
concrete FunASR / VoxCPM2 / CosyVoice implementation, so swapping a
voice backend is a config edit rather than a code change.

The three capabilities are deliberately minimal:

* **input** — capture from the microphone and transcribe one utterance;
* **output** — synthesize text and play it back;
* **cancel** — stop an in-flight capture or playback on demand.

``adapters/protocols.py`` keeps the orchestration-facing surface
(``TTSBackend`` / ``STTBackend``); the protocols here are the plugin seam
that concrete provider modules implement. The two must stay in sync:
signature parity is enforced by tests.
"""

from __future__ import annotations

from typing import Any, Iterator, Protocol, runtime_checkable


@runtime_checkable
class SpeechInputProvider(Protocol):
    """Microphone capture + transcription (ASR).

    Implementations: ``services.stt_service.STTService`` (FunASR).
    """

    def load_model(self, progress_callback: Any = None) -> Any:
        """Load the recognition model. ``progress_callback`` is optional."""
        ...

    def warmup(self) -> bool:
        """Return whether the provider is ready to transcribe."""
        ...

    def start_recording(self) -> None:
        """Open the microphone and start capturing one utterance."""
        ...

    def stop_recording(self) -> Any:
        """Stop capturing and return the recorded audio payload."""
        ...

    def transcribe(self, audio: Any) -> str:
        """Transcribe one recorded utterance into text."""
        ...

    def is_vad_triggered(self) -> bool:
        """Whether voice-activity endpointing has fired for this capture."""
        ...

    def cancel(self) -> None:
        """Abandon an in-flight capture without producing audio."""
        ...


@runtime_checkable
class SpeechOutputProvider(Protocol):
    """Text-to-speech synthesis + playback (TTS).

    Implementations: ``services.tts_service_voxcpm.TTSService`` (VoxCPM2),
    ``services.tts_service_cosyvoice.TTSService`` (CosyVoice).
    """

    def load_model(self, progress_callback: Any = None, **kwargs: Any) -> Any:
        """Load the synthesis model. ``progress_callback`` is optional."""
        ...

    def warmup(self) -> bool:
        """Return whether the provider produced usable audio."""
        ...

    def generate_and_play(self, text: str, **kwargs: Any) -> Any:
        """Synthesize and play one utterance; returns a playback result."""
        ...

    def generate(self, text: str, **kwargs: Any) -> Any:
        """Synthesize one utterance offline, returning an audio payload."""
        ...

    def stop_playing(self) -> None:
        """Stop the current playback."""
        ...

    def cancel(self) -> None:
        """Cancel the current synthesis/playback (alias of stop)."""
        ...


def iter_audio_frames(stream: Iterator[Any]) -> Iterator[Any]:
    """Pass-through helper for providers that expose frame iterators.

    Kept trivial on purpose: cancellation of a frame stream is the
    provider's responsibility (close the underlying generator).
    """
    for frame in stream:
        yield frame
