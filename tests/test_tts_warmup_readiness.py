"""Tracked regressions for the TTS-12 warmup/readiness contract."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from services.tts_service_voxcpm import TTSService


@pytest.mark.parametrize(
    ("generated", "expected"),
    [
        (np.ones(8, dtype=np.float32), True),
        (np.array([], dtype=np.float32), False),
        (None, False),
    ],
    ids=["non-empty", "empty-array", "none"],
)
def test_warmup_returns_readiness_for_generated_audio(generated, expected):
    service = TTSService.__new__(TTSService)
    service.generate = lambda _text: generated

    assert service.warmup() is expected


def test_warmup_returns_false_when_generation_raises():
    service = TTSService.__new__(TTSService)

    def failing_generate(_text):
        raise RuntimeError("warmup provider failure")

    service.generate = failing_generate

    assert service.warmup() is False


def test_mainwindow_consumes_warmup_result_and_retires_failed_service():
    source = (Path(__file__).resolve().parents[1] / "ui" / "main_window.py").read_text(
        encoding="utf-8"
    )
    warmup_call = source.index("self.tts_service.warmup()")
    load_tail = source[warmup_call : warmup_call + 1800]

    assert "warmup_ok" in load_tail
    assert "self.tts_service = None" in load_tail
    assert "tts_ok = False" in load_tail
    assert "语音合成不可用" in load_tail
