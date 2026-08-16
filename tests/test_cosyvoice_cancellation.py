"""Deterministic compatibility tests for CosyVoice cancellation cleanup."""

from __future__ import annotations

import queue
import threading
import time
from types import SimpleNamespace

import numpy as np

import services.tts_service_cosyvoice as cosyvoice_module
from services.tts_service_cosyvoice import TTSService


class _Speech:
    def __init__(self, values):
        self.values = np.asarray(values, dtype=np.float32)

    def squeeze(self):
        return self

    def float(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.values

    def tobytes(self):
        return self.values.tobytes()


def _chunk(value: float, size: int = 8):
    return {"tts_speech": _Speech([value] * size)}


class _Stream:
    def __init__(self, *, fail_writes: bool = False):
        self.writes: list[bytes] = []
        self.fail_writes = fail_writes
        self.stopped = False
        self.closed = False

    def is_active(self):
        return not self.closed and not self.stopped

    def write(self, data):
        if self.fail_writes:
            self.fail_writes = False
            raise RuntimeError("synthetic stream write failure")
        self.writes.append(data)

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True


class _PyAudio:
    def __init__(self, stream: _Stream):
        self.stream = stream
        self.open_calls = 0
        self.terminated = False
        self.terminate_seen_stop = False

    def open(self, **_kwargs):
        self.open_calls += 1
        return self.stream

    def terminate(self):
        self.terminate_seen_stop = self.stream.stopped
        self.terminated = True


class _Model:
    def __init__(self, chunks, *, error: Exception | None = None, block_after=False):
        self.chunks = list(chunks)
        self.error = error
        self.block_after = block_after
        self.started = threading.Event()
        self.release = threading.Event()

    def inference_zero_shot(self, **_kwargs):
        self.started.set()
        for item in self.chunks:
            yield item
        if self.error is not None:
            raise self.error
        if self.block_after:
            self.release.wait(timeout=3.0)


def _service(tmp_path, model, *, prompt_exists=True, stream=None):
    if cosyvoice_module.pyaudio is None:
        cosyvoice_module.pyaudio = SimpleNamespace(paFloat32=1)
    service = TTSService.__new__(TTSService)
    service.model = model
    service.sample_rate = 10
    service.prompt_text = "提示音频"
    prompt = tmp_path / "prompt.wav"
    if prompt_exists:
        prompt.write_bytes(b"prompt")
    service.prompt_wav = str(prompt)
    service._use_cached_speaker = False
    service.is_playing = False
    service.stream = None
    service.pyaudio = _PyAudio(stream or _Stream())
    service._play_lock = threading.Lock()
    return service


def _run_inner(service, holder):
    holder.append(service._generate_and_play_inner("你好。"))


def _wait_until(predicate, timeout=1.0):
    deadline = threading.Event()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        deadline.wait(0.005)
    return predicate()


def test_cosy01_normal_eof_flushes_prebuffered_chunks(tmp_path):
    service = _service(tmp_path, _Model([]))
    stream = _Stream()
    stop_event = threading.Event()
    playback_queue = queue.Queue()
    for value in (1, 2, 3):
        playback_queue.put(_Speech([value]))
    playback_queue.put(None)

    worker = threading.Thread(
        target=service._playback_worker,
        args=(playback_queue, stream, stop_event),
        kwargs={"pre_buffer": 5},
    )
    worker.start()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert len(stream.writes) == 3


def test_cosy02_cancel_before_prebuffer_discards_buffered_chunks(tmp_path):
    service = _service(tmp_path, _Model([]))
    stream = _Stream()
    stop_event = threading.Event()
    playback_queue = queue.Queue()
    playback_queue.put(_Speech([1]))

    worker = threading.Thread(
        target=service._playback_worker,
        args=(playback_queue, stream, stop_event),
        kwargs={"pre_buffer": 5},
    )
    worker.start()
    assert _wait_until(playback_queue.empty)
    stop_event.set()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert stream.writes == []


def test_cosy03_cancel_after_playback_does_not_write_queued_tail(tmp_path):
    service = _service(tmp_path, _Model([]))
    stream = _Stream()
    stop_event = threading.Event()
    playback_queue = queue.Queue()
    playback_queue.put(_Speech([1]))
    playback_queue.put(_Speech([2]))
    worker = threading.Thread(
        target=service._playback_worker,
        args=(playback_queue, stream, stop_event),
        kwargs={"pre_buffer": 2},
    )
    worker.start()
    assert _wait_until(lambda: len(stream.writes) == 2)
    stop_event.set()
    playback_queue.put(_Speech([3]))
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert len(stream.writes) == 2


def test_cosy04_stream_write_failure_does_not_flush_stale_buffer(tmp_path):
    service = _service(tmp_path, _Model([]))
    stream = _Stream(fail_writes=True)
    stop_event = threading.Event()
    playback_queue = queue.Queue()
    for value in (1, 2, 3):
        playback_queue.put(_Speech([value]))
    playback_queue.put(None)

    worker = threading.Thread(
        target=service._playback_worker,
        args=(playback_queue, stream, stop_event),
        kwargs={"pre_buffer": 3},
    )
    worker.start()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert stream.writes == []


def test_cosy05_stop_playing_sets_request_local_stop_event(tmp_path):
    model = _Model([_chunk(1)], block_after=True)
    service = _service(tmp_path, model)
    holder = []
    worker = threading.Thread(target=_run_inner, args=(service, holder), daemon=True)
    worker.start()
    assert model.started.wait(timeout=2.0)
    assert _wait_until(lambda: service._active_playback is not None)

    service.stop_playing()
    state = service._active_playback
    assert state is not None
    assert state.stop_event.is_set()
    model.release.set()
    worker.join(timeout=3.0)
    assert not worker.is_alive()


def test_cosy06_stop_playing_is_idempotent_without_active_request(tmp_path):
    service = _service(tmp_path, _Model([]))
    service.stop_playing()
    service.stop_playing()


def test_cosy07_missing_prompt_does_not_open_stream_or_start_worker(tmp_path):
    service = _service(tmp_path, _Model([]), prompt_exists=False)

    service._generate_and_play_inner("你好。")

    assert service.pyaudio.open_calls == 0
    assert service._active_playback is None


def test_cosy08_provider_exception_converges_worker_and_stream_cleanup(tmp_path):
    stream = _Stream()
    model = _Model([_chunk(1)], error=RuntimeError("provider failed"))
    service = _service(tmp_path, model, stream=stream)

    service._generate_and_play_inner("你好。")

    assert stream.closed
    assert service._active_playback is None


def test_cosy09_queue_admission_rechecks_cancellation(tmp_path):
    service = _service(tmp_path, _Model([]))
    playback_queue = queue.Queue(maxsize=1)
    playback_queue.put("occupied")
    stop_event = threading.Event()
    stop_event.set()

    assert not service._put_playback_chunk(playback_queue, "new", stop_event)


def test_cosy10_late_cleanup_cannot_clear_new_request(tmp_path):
    service = _service(tmp_path, _Model([]))
    state_a = service._new_playback_state()
    state_b = service._new_playback_state()
    stream_a = _Stream()
    stream_b = _Stream()
    state_a.stream = stream_a
    state_b.stream = stream_b
    service._set_active_playback(state_b)

    service._clear_active_playback(state_a)
    service._close_stream(stream_a, state=state_a)

    assert service._active_playback is state_b
    assert not stream_b.closed


def test_cosy11_cleanup_cancels_before_terminating_pyaudio(tmp_path):
    model = _Model([_chunk(1)], block_after=True)
    service = _service(tmp_path, model)
    worker = threading.Thread(target=service._generate_and_play_inner, args=("你好。",), daemon=True)
    worker.start()
    assert model.started.wait(timeout=2.0)
    assert _wait_until(lambda: service._active_playback is not None)

    service.cleanup()
    model.release.set()
    worker.join(timeout=3.0)

    assert not worker.is_alive()
    assert service.pyaudio is None


def test_cosy12_cleanup_is_safe_when_called_twice(tmp_path):
    service = _service(tmp_path, _Model([]))
    service.cleanup()
    service.cleanup()


def test_cosy13_cosyvoice_is_not_production_selector():
    source = (cosyvoice_module.__file__ or "")
    selector = open(source.rsplit("services", 1)[0] + "services/tts_service.py", encoding="utf-8").read()
    assert "services.tts_service_voxcpm" in selector
    assert "tts_service_cosyvoice" not in selector
