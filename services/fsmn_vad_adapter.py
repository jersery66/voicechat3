"""Streaming FunASR FSMN-VAD adapter.

This module deliberately owns only the VAD model and its streaming cache.
Recording lifecycle decisions remain in :mod:`services.stt_service`.
"""

from __future__ import annotations

from enum import Enum
import logging
from numbers import Real
from typing import Any, Callable, Iterable

import numpy as np


logger = logging.getLogger(__name__)


class VadEvent(Enum):
    """Normalized endpoint events emitted by the provider-specific adapter."""

    NONE = "none"
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"


class FSMNVADAdapter:
    """Small stateful wrapper around FunASR's streaming ``fsmn-vad`` model.

    FunASR 1.3.x returns streaming segments in the form ``[beg, end]`` where
    either endpoint may be ``-1``.  The adapter normalizes all supported
    result envelopes and keeps the model cache private to this stream.
    """

    MODEL_ID = "fsmn-vad"
    SAMPLE_RATE = 16000
    CHUNK_MS = 200
    CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_MS // 1000

    def __init__(
        self,
        *,
        device: str = "cpu",
        model_factory: Callable[..., Any] | None = None,
        model_id: str = MODEL_ID,
    ):
        self.device = device
        self.model_id = model_id
        self._model_factory = model_factory
        self.model: Any | None = None
        self.cache: dict[str, Any] = {}
        self._speech_seen = False
        self._speech_active = False

    @property
    def speech_seen(self) -> bool:
        """Whether this recording has produced a speech-start boundary."""
        return self._speech_seen

    @property
    def speech_active(self) -> bool:
        return self._speech_active

    def load(self) -> bool:
        """Load ``AutoModel(model='fsmn-vad')`` on the configured device."""
        if self.model is not None:
            return True

        factory = self._model_factory
        if factory is None:
            from funasr import AutoModel

            factory = AutoModel

        logger.info(
            "Loading FSMN_VAD backend model=%s device=%s chunk_ms=%s",
            self.model_id,
            self.device,
            self.CHUNK_MS,
        )
        self.model = factory(
            model=self.model_id,
            device=self.device,
            disable_update=True,
        )
        self.reset()
        return True

    def reset(self) -> None:
        """Reset provider cache and speech state for a new recording."""
        self.cache.clear()
        self._speech_seen = False
        self._speech_active = False

    def close(self) -> None:
        """Release the adapter's model reference and streaming cache."""
        self.reset()
        self.model = None

    def feed(self, audio_chunk: np.ndarray, *, is_final: bool = False) -> VadEvent:
        """Feed one ordered PCM chunk and normalize its endpoint event."""
        if self.model is None:
            raise RuntimeError("FSMN-VAD model is not loaded")

        chunk = np.asarray(audio_chunk, dtype=np.float32).reshape(-1)
        if chunk.size == 0:
            return VadEvent.NONE

        result = self.model.generate(
            input=chunk,
            cache=self.cache,
            is_final=is_final,
            chunk_size=self.CHUNK_MS,
        )
        segments = list(self._iter_segments(result))

        saw_begin = False
        saw_end = False
        for begin, end in segments:
            if begin >= 0:
                saw_begin = True
            if end >= 0:
                saw_end = True

        # A complete [beg, end] segment is an endpoint in a live recorder.
        # Prefer END when one provider call reports both boundaries.
        if saw_end:
            self._speech_seen = self._speech_seen or saw_begin
            self._speech_active = False
            return VadEvent.SPEECH_END
        if saw_begin:
            self._speech_seen = True
            self._speech_active = True
            return VadEvent.SPEECH_START
        return VadEvent.NONE

    @classmethod
    def _iter_segments(cls, result: Any) -> Iterable[tuple[float, float]]:
        """Yield ``(begin_ms, end_ms)`` pairs from FunASR result envelopes."""
        if result is None:
            return
        if isinstance(result, dict):
            if "value" in result:
                yield from cls._iter_segments(result["value"])
                return
            for value in result.values():
                yield from cls._iter_segments(value)
            return
        if isinstance(result, np.ndarray):
            result = result.tolist()
        if isinstance(result, (list, tuple)):
            if len(result) == 2 and all(isinstance(item, Real) for item in result):
                yield float(result[0]), float(result[1])
                return
            for value in result:
                yield from cls._iter_segments(value)
