"""Deterministic cancellation tests for the explicit VoxCPM OutputStream."""

from __future__ import annotations

import threading
import time

import numpy as np

from adapters.tts_results import PlaybackStatus
from services.tts_service_voxcpm import TTSService


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class _Chunk:
    def __init__(self, samples: int = 16):
        self.samples = samples
        self.astype_calls = 0

    def astype(self, _dtype):
        self.astype_calls += 1
        return np.ones(self.samples, dtype=np.float32)


class _ControlledModel:
    def __init__(self, *, hold_after_first: bool = True, wait_before_first: bool = False):
        self.first = _Chunk()
        self.second = _Chunk()
        self.hold_after_first = hold_after_first
        self.wait_before_first = wait_before_first
        self.started = threading.Event()
        self.release = threading.Event()
        self.first_yielded = threading.Event()

    def generate_streaming(self, **_kwargs):
        self.started.set()
        if self.wait_before_first:
            self.release.wait(timeout=2.0)
        yield self.first
        self.first_yielded.set()
        if self.hold_after_first:
            self.release.wait(timeout=2.0)
        yield self.second


class _AbortableStream:
    instances: list["_AbortableStream"] = []

    def __init__(self, *, callback, **_kwargs):
        self.callback = callback
        self.created = threading.Event()
        self.entered = threading.Event()
        self.exited = threading.Event()
        self.abort_calls = 0
        self.raise_on_abort = False
        self.created.set()
        type(self).instances.append(self)

    def __enter__(self):
        self.entered.set()
        return self

    def __exit__(self, *_args):
        self.exited.set()
        return False

    def abort(self):
        self.abort_calls += 1
        if self.raise_on_abort:
            raise RuntimeError("abort failed")


def _service(monkeypatch, model):
    service = TTSService.__new__(TTSService)
    service.model = model
    service.sample_rate = 10
    service.prompt_cache = None
    service.is_playing = False
    service._play_lock = threading.Lock()
    service._active_playback = None
    service._active_playback_lock = threading.RLock()
    _AbortableStream.instances.clear()
    monkeypatch.setattr("services.tts_service_voxcpm.sd.OutputStream", _AbortableStream)
    monkeypatch.setattr("services.tts_service_voxcpm.sd.stop", lambda: None)
    return service


def _start_inner(service):
    result_holder = []
    worker = threading.Thread(
        target=lambda: result_holder.append(service._generate_and_play_inner("你好。")),
        daemon=True,
    )
    worker.start()
    return worker, result_holder


def test_active_explicit_stream_is_published_and_abortable(monkeypatch):
    model = _ControlledModel()
    service = _service(monkeypatch, model)
    worker, result_holder = _start_inner(service)

    assert model.first_yielded.wait(timeout=2.0)
    assert _wait_for(lambda: bool(_AbortableStream.instances))
    stream = _AbortableStream.instances[0]
    assert _wait_for(lambda: service._active_playback is not None)
    assert service._active_playback.stream is stream

    service.stop_playing()
    model.release.set()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert stream.abort_calls == 1
    assert stream.exited.is_set()
    assert result_holder[0].status is PlaybackStatus.CANCELLED


def test_public_generate_and_play_cancels_while_play_lock_is_held(monkeypatch):
    model = _ControlledModel()
    service = _service(monkeypatch, model)
    result_holder = []
    worker = threading.Thread(
        target=lambda: result_holder.append(service.generate_and_play("你好。")),
        daemon=True,
    )
    worker.start()
    assert _wait_for(lambda: bool(_AbortableStream.instances))

    service.stop_playing()
    model.release.set()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert result_holder[0].status is PlaybackStatus.CANCELLED


def test_stop_playing_does_not_use_module_level_stop_for_explicit_stream(monkeypatch):
    model = _ControlledModel()
    service = _service(monkeypatch, model)
    stop_calls = []
    monkeypatch.setattr("services.tts_service_voxcpm.sd.stop", lambda: stop_calls.append(True))
    worker, result_holder = _start_inner(service)
    assert _wait_for(lambda: bool(_AbortableStream.instances))

    service.stop_playing()
    model.release.set()
    worker.join(timeout=2.0)

    assert stop_calls == []
    assert result_holder[0].status is PlaybackStatus.CANCELLED


def test_cancel_before_stream_construction_prevents_playback(monkeypatch):
    model = _ControlledModel(wait_before_first=True)
    service = _service(monkeypatch, model)
    worker, result_holder = _start_inner(service)
    assert model.started.wait(timeout=2.0)

    service.stop_playing()
    model.release.set()
    worker.join(timeout=2.0)

    assert not _AbortableStream.instances
    assert result_holder[0].status is PlaybackStatus.CANCELLED


def test_repeated_stop_is_idempotent_and_abort_failure_stays_cancelled(monkeypatch):
    model = _ControlledModel()
    service = _service(monkeypatch, model)
    worker, result_holder = _start_inner(service)
    assert _wait_for(lambda: bool(_AbortableStream.instances))
    stream = _AbortableStream.instances[0]
    stream.raise_on_abort = True

    service.stop_playing()
    service.stop_playing()
    model.release.set()
    worker.join(timeout=2.0)

    assert stream.abort_calls == 1
    assert result_holder[0].status is PlaybackStatus.CANCELLED


def test_cancellation_does_not_convert_unread_buffer_into_playback(monkeypatch):
    model = _ControlledModel()
    service = _service(monkeypatch, model)
    worker, result_holder = _start_inner(service)
    assert _wait_for(lambda: bool(_AbortableStream.instances))
    stream = _AbortableStream.instances[0]

    service.stop_playing()
    model.release.set()
    worker.join(timeout=2.0)

    assert stream.exited.is_set()
    assert model.first.astype_calls == 1
    assert model.second.astype_calls == 0
    assert result_holder[0].status is PlaybackStatus.CANCELLED


def test_cancellation_cannot_clear_a_new_active_playback_state(monkeypatch):
    old_model = _ControlledModel()
    service = _service(monkeypatch, old_model)
    old_worker, old_result = _start_inner(service)
    assert _wait_for(lambda: bool(_AbortableStream.instances))
    old_state = service._active_playback
    service.stop_playing()
    old_model.release.set()
    old_worker.join(timeout=2.0)
    assert old_result[0].status is PlaybackStatus.CANCELLED

    new_model = _ControlledModel()
    new_worker, new_result = _start_inner(service)
    assert _wait_for(lambda: service._active_playback is not None)
    new_state = service._active_playback
    assert new_state is not old_state
    service._clear_active_playback(old_state)
    assert service._active_playback is new_state

    service.stop_playing()
    new_model.release.set()
    new_worker.join(timeout=2.0)
    assert new_result[0].status is PlaybackStatus.CANCELLED


def test_cancelled_worker_exits_and_next_request_has_new_stream(monkeypatch):
    first_model = _ControlledModel()
    service = _service(monkeypatch, first_model)
    first_worker, first_result = _start_inner(service)
    assert _wait_for(lambda: bool(_AbortableStream.instances))
    first_stream = _AbortableStream.instances[0]
    service.stop_playing()
    first_model.release.set()
    first_worker.join(timeout=2.0)
    assert first_result[0].status is PlaybackStatus.CANCELLED
    assert first_stream.exited.is_set()

    second_model = _ControlledModel()
    second_worker, second_result = _start_inner(service)
    assert _wait_for(lambda: len(_AbortableStream.instances) >= 2)
    second_stream = _AbortableStream.instances[1]
    assert second_stream is not first_stream
    service.stop_playing()
    second_model.release.set()
    second_worker.join(timeout=2.0)
    assert second_result[0].status is PlaybackStatus.CANCELLED
