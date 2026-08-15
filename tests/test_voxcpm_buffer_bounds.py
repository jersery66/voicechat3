"""Regression tests for the bounded VoxCPM playback ring buffer."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from adapters.tts_results import PlaybackStatus
from services.tts_service_voxcpm import TTSService


class _Wav:
    def __init__(self, samples: np.ndarray):
        self._samples = samples

    def squeeze(self, _axis):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self._samples


class _OrderedModel:
    def __init__(self, *, prompt_cache: bool, chunks: list[np.ndarray]):
        self.prompt_cache = prompt_cache
        self.chunks = chunks
        self.first_yielded = threading.Event()
        self.second_yielded = threading.Event()
        self.completed = threading.Event()
        self.allow_second = threading.Event()
        if prompt_cache:
            self.tts_model = self

    def _items(self):
        for index, chunk in enumerate(self.chunks):
            if index == 1:
                self.allow_second.wait(timeout=2.0)
                self.second_yielded.set()
            if self.prompt_cache:
                yield _Wav(chunk), None, None
            else:
                yield chunk
            if index == 0:
                self.first_yielded.set()
        self.completed.set()

    def generate_streaming(self, **_kwargs):
        yield from self._items()

    def _generate_with_prompt_cache(self, **_kwargs):
        yield from self._items()


class _RecordingStream:
    """Explicit OutputStream fake with a controllable consumer."""

    instances: list["_RecordingStream"] = []

    def __init__(self, *, callback, **_kwargs):
        self.callback = callback
        self.consume = threading.Event()
        self.stop = threading.Event()
        self.outputs: list[np.ndarray] = []
        self.abort_calls = 0
        self._thread: threading.Thread | None = None
        type(self).instances.append(self)

    def __enter__(self):
        def _run():
            self.consume.wait(timeout=3.0)
            while not self.stop.is_set():
                outdata = np.zeros((64, 1), dtype=np.float32)
                try:
                    self.callback(outdata, 64, None, None)
                except Exception:
                    break
                self.outputs.append(outdata.copy())
                time.sleep(0.001)

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args):
        self.stop.set()
        self.consume.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        return False

    def abort(self):
        self.abort_calls += 1
        self.stop.set()
        self.consume.set()

    def close(self):
        self.stop.set()
        self.consume.set()


class _BrokenStream:
    def __init__(self, **_kwargs):
        raise RuntimeError("output worker failed")


class _SingleChunkModel:
    prompt_cache = False

    def __init__(self, chunk: np.ndarray):
        self.chunk = chunk
        self.yielded = threading.Event()
        self.completed = threading.Event()

    def generate_streaming(self, **_kwargs):
        self.yielded.set()
        yield self.chunk
        self.completed.set()


def _service(monkeypatch, model):
    service = TTSService.__new__(TTSService)
    service.model = model
    service.sample_rate = 10
    service.prompt_cache = object() if model.prompt_cache else None
    service.is_playing = False
    service._play_lock = threading.Lock()
    service._active_playback = None
    service._active_playback_lock = threading.RLock()
    _RecordingStream.instances.clear()
    monkeypatch.setattr("services.tts_service_voxcpm.sd.OutputStream", _RecordingStream)
    monkeypatch.setattr("services.tts_service_voxcpm.sd.stop", lambda: None)
    return service


def _start(service):
    result: list = []
    worker = threading.Thread(
        target=lambda: result.append(service._generate_and_play_inner("你好。")),
        daemon=True,
    )
    worker.start()
    return worker, result


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


@pytest.mark.parametrize("prompt_cache", [False, True])
def test_bounded_ring_waits_for_capacity_and_preserves_order(monkeypatch, prompt_cache):
    first = np.ones(1200, dtype=np.float32)
    second = np.full(800, 2.0, dtype=np.float32)
    model = _OrderedModel(prompt_cache=prompt_cache, chunks=[first, second])
    service = _service(monkeypatch, model)
    worker, result = _start(service)

    assert model.first_yielded.wait(timeout=2.0)
    assert _wait_for(lambda: bool(_RecordingStream.instances))
    stream = _RecordingStream.instances[0]
    model.allow_second.set()
    assert model.second_yielded.wait(timeout=2.0)

    # The producer must still be waiting for the full ring to drain.  The
    # old implementation overwrote the prefix and completed immediately.
    assert not model.completed.wait(timeout=0.1)
    stream.consume.set()

    worker.join(timeout=3.0)
    assert not worker.is_alive()
    assert result[0].status is PlaybackStatus.COMPLETED

    samples = np.concatenate(stream.outputs) if stream.outputs else np.array([])
    nonzero = samples[samples != 0]
    assert nonzero.size >= first.size + second.size
    np.testing.assert_array_equal(nonzero[: first.size], first)
    np.testing.assert_array_equal(nonzero[first.size : first.size + second.size], second)


def test_bounded_ring_cancellation_unblocks_full_producer(monkeypatch):
    first = np.ones(1200, dtype=np.float32)
    second = np.full(800, 2.0, dtype=np.float32)
    model = _OrderedModel(prompt_cache=False, chunks=[first, second])
    service = _service(monkeypatch, model)
    worker, result = _start(service)

    assert model.first_yielded.wait(timeout=2.0)
    model.allow_second.set()
    assert model.second_yielded.wait(timeout=2.0)
    assert not model.completed.is_set()

    service.stop_playing()
    worker.join(timeout=3.0)
    assert not worker.is_alive()
    assert result[0].status is PlaybackStatus.CANCELLED


def test_bounded_ring_accepts_a_chunk_larger_than_capacity_incrementally(monkeypatch):
    chunk = np.arange(2000, dtype=np.float32) + 1.0
    model = _SingleChunkModel(chunk)
    service = _service(monkeypatch, model)
    worker, result = _start(service)

    assert model.yielded.wait(timeout=2.0)
    assert _wait_for(lambda: bool(_RecordingStream.instances))
    stream = _RecordingStream.instances[0]
    assert not model.completed.wait(timeout=0.1)
    stream.consume.set()

    worker.join(timeout=3.0)
    assert not worker.is_alive()
    assert result[0].status is PlaybackStatus.COMPLETED
    samples = np.concatenate(stream.outputs) if stream.outputs else np.array([])
    nonzero = samples[samples != 0]
    np.testing.assert_array_equal(nonzero[: chunk.size], chunk)


def test_bounded_ring_worker_failure_unblocks_full_producer(monkeypatch):
    first = np.ones(1200, dtype=np.float32)
    second = np.full(800, 2.0, dtype=np.float32)
    model = _OrderedModel(prompt_cache=False, chunks=[first, second])
    service = _service(monkeypatch, model)
    monkeypatch.setattr("services.tts_service_voxcpm.sd.OutputStream", _BrokenStream)
    worker, result = _start(service)

    model.allow_second.set()
    assert model.second_yielded.wait(timeout=2.0)
    worker.join(timeout=3.0)
    assert not worker.is_alive()
    assert result[0].status is PlaybackStatus.FAILED
    assert result[0].error.startswith("output_worker_error")


def test_bounded_ring_normal_playback_still_completes(monkeypatch):
    model = _SingleChunkModel(np.ones(32, dtype=np.float32))
    service = _service(monkeypatch, model)
    worker, result = _start(service)
    assert _wait_for(lambda: bool(_RecordingStream.instances))
    _RecordingStream.instances[0].consume.set()
    worker.join(timeout=3.0)
    assert not worker.is_alive()
    assert result[0].status is PlaybackStatus.COMPLETED
