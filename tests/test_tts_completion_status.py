"""Focused tests for explicit VoxCPM playback completion semantics."""

from __future__ import annotations

import threading
import time
import inspect

import numpy as np
import pytest

from adapters.tts_results import PlaybackResult, PlaybackStatus
from adapters.protocols import TTSBackend
from conversation.delivery import (
    AudioFinished,
    DeliveryLedger,
    GenerationController,
    SentenceDeliveryQueue,
    SentenceReady,
)
from services.tts_service_voxcpm import TTSService


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _ResultTTS:
    def __init__(self, result):
        self.result = result
        self.calls: list[str] = []
        self.stop_calls = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def generate_and_play(self, text: str):
        self.calls.append(text)
        self.started.set()
        self.release.wait(timeout=2.0)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    def stop_playing(self):
        self.stop_calls += 1
        self.release.set()


def _play_one(result):
    controller = GenerationController()
    record = controller.start_generation()
    events = []
    tts = _ResultTTS(result)
    queue = SentenceDeliveryQueue(controller, tts, on_event=events.append)
    queue.start()
    assert queue.enqueue(SentenceReady(record.generation_id, 0, "一句话。"))
    tts.release.set()
    assert _wait_for(lambda: any(isinstance(event, AudioFinished) for event in events))
    queue.shutdown()
    return events


def test_playback_status_has_exact_three_values_and_ok_only_for_completed():
    assert {status.value for status in PlaybackStatus} == {
        "completed",
        "cancelled",
        "failed",
    }
    assert PlaybackResult(PlaybackStatus.COMPLETED).ok is True
    assert PlaybackResult(PlaybackStatus.CANCELLED).ok is False
    assert PlaybackResult(PlaybackStatus.FAILED, "error").ok is False


def test_tts_backend_declares_explicit_playback_result():
    assert inspect.signature(TTSBackend.generate_and_play).return_annotation is PlaybackResult


@pytest.mark.parametrize("status", list(PlaybackStatus))
def test_delivery_queue_emits_explicit_provider_status(status):
    events = _play_one(PlaybackResult(status))
    finished = next(event for event in events if isinstance(event, AudioFinished))
    assert finished.status is status
    assert finished.ok is (status is PlaybackStatus.COMPLETED)


def test_delivery_queue_maps_missing_provider_result_to_failed():
    events = _play_one(None)
    finished = next(event for event in events if isinstance(event, AudioFinished))
    assert finished.status is PlaybackStatus.FAILED
    assert finished.ok is False


def test_delivery_queue_maps_unknown_provider_status_to_failed():
    events = _play_one(PlaybackResult("unknown"))
    finished = next(event for event in events if isinstance(event, AudioFinished))
    assert finished.status is PlaybackStatus.FAILED
    assert finished.ok is False


def test_delivery_queue_maps_provider_exception_to_failed_without_raising():
    events = _play_one(RuntimeError("provider failed"))
    finished = next(event for event in events if isinstance(event, AudioFinished))
    assert finished.status is PlaybackStatus.FAILED
    assert finished.ok is False


def test_stale_generation_suppresses_audio_finished_status_callback():
    controller = GenerationController()
    record = controller.start_generation()
    events = []
    tts = _ResultTTS(PlaybackResult(PlaybackStatus.COMPLETED))
    queue = SentenceDeliveryQueue(controller, tts, on_event=events.append)
    queue.start()
    assert queue.enqueue(SentenceReady(record.generation_id, 0, "会被取消。"))
    assert tts.started.wait(timeout=2.0)
    controller.cancel_generation(record.generation_id, reason="new turn")
    tts.release.set()
    assert _wait_for(lambda: not queue._thread or not queue._thread.is_alive()) is False
    time.sleep(0.05)
    queue.shutdown()
    assert not any(isinstance(event, AudioFinished) for event in events)


class _HistoryOwner:
    def __init__(self):
        self.conversation_history = [{"role": "user", "content": "你好"}]


def test_tts_failure_does_not_change_visible_or_history_state():
    controller = GenerationController()
    record = controller.start_generation()
    ledger = DeliveryLedger(controller)
    assert ledger.commit_visible(SentenceReady(record.generation_id, 0, "已经显示。"))
    tts = _ResultTTS(PlaybackResult(PlaybackStatus.FAILED, "audio error"))
    queue = SentenceDeliveryQueue(controller, tts)
    queue.start()
    assert queue.enqueue(SentenceReady(record.generation_id, 0, "已经显示。"))
    tts.release.set()
    assert _wait_for(lambda: not queue._active)
    queue.shutdown()
    owner = _HistoryOwner()
    assert ledger.delivered_text(record.generation_id) == "已经显示。"
    assert owner.conversation_history == [{"role": "user", "content": "你好"}]


class _FakeOutputStream:
    """Small callback-driving sounddevice stand-in for provider unit tests."""

    def __init__(self, *, callback, **_kwargs):
        self.callback = callback
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        def consume():
            while not self._stop.is_set():
                outdata = np.zeros((64, 1), dtype=np.float32)
                try:
                    self.callback(outdata, 64, None, None)
                except Exception:
                    break
                time.sleep(0.001)

        self._thread = threading.Thread(target=consume, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        return False


class _StreamingModel:
    def __init__(self, chunks):
        self.chunks = chunks

    def generate_streaming(self, **_kwargs):
        for chunk in self.chunks:
            yield chunk


def _provider_service(monkeypatch, chunks):
    service = TTSService.__new__(TTSService)
    service.model = _StreamingModel(chunks)
    service.sample_rate = 10
    service.prompt_cache = None
    service.is_playing = False
    monkeypatch.setattr("services.tts_service_voxcpm.sd.OutputStream", _FakeOutputStream)
    return service


def test_voxcpm_empty_text_is_failed(monkeypatch):
    service = _provider_service(monkeypatch, [np.ones(16, dtype=np.float32)])
    result = service._generate_and_play_inner("   ")
    assert result.status is PlaybackStatus.FAILED
    assert result.error == "empty_text"


def test_voxcpm_normal_audio_is_completed(monkeypatch):
    service = _provider_service(monkeypatch, [np.ones(16, dtype=np.float32)])
    result = service._generate_and_play_inner("你好。")
    assert result.status is PlaybackStatus.COMPLETED


def test_voxcpm_zero_generated_samples_is_failed(monkeypatch):
    service = _provider_service(monkeypatch, [np.array([], dtype=np.float32)])
    result = service._generate_and_play_inner("你好。")
    assert result.status is PlaybackStatus.FAILED
    assert result.error == "no_audio"


def test_voxcpm_provider_exception_is_failed(monkeypatch):
    class BrokenModel:
        def generate_streaming(self, **_kwargs):
            raise RuntimeError("generation failed")
            yield  # pragma: no cover

    service = _provider_service(monkeypatch, [])
    service.model = BrokenModel()
    result = service._generate_and_play_inner("你好。")
    assert result.status is PlaybackStatus.FAILED
    assert result.error.startswith("generation_error")


def test_voxcpm_output_worker_exception_is_failed(monkeypatch):
    class BrokenOutputStream:
        def __init__(self, **_kwargs):
            raise RuntimeError("audio device failed")

    service = _provider_service(monkeypatch, [np.ones(16, dtype=np.float32)])
    monkeypatch.setattr(
        "services.tts_service_voxcpm.sd.OutputStream", BrokenOutputStream
    )
    result = service._generate_and_play_inner("你好。")
    assert result.status is PlaybackStatus.FAILED
    assert result.error.startswith("output_worker_error")


def test_voxcpm_stop_before_normal_completion_is_cancelled(monkeypatch):
    class StoppableModel:
        started = threading.Event()
        release = threading.Event()

        def generate_streaming(self, **_kwargs):
            yield np.ones(16, dtype=np.float32)
            self.started.set()
            self.release.wait(timeout=2.0)
            yield np.ones(16, dtype=np.float32)

    model = StoppableModel()
    service = _provider_service(monkeypatch, [])
    service.model = model
    monkeypatch.setattr("services.tts_service_voxcpm.sd.stop", lambda: None)
    result_holder = []
    worker = threading.Thread(
        target=lambda: result_holder.append(service._generate_and_play_inner("你好。")),
        daemon=True,
    )
    worker.start()
    assert model.started.wait(timeout=2.0)
    service.stop_playing()
    model.release.set()
    worker.join(timeout=3.0)
    assert not worker.is_alive()
    assert result_holder[0].status is PlaybackStatus.CANCELLED
