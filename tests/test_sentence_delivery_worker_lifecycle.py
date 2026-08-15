"""Regression tests for SentenceDeliveryQueue worker ownership."""

from __future__ import annotations

import threading
import time

from adapters.tts_results import PlaybackResult, PlaybackStatus
from conversation.delivery import (
    AudioFinished,
    GenerationController,
    SentenceDeliveryQueue,
    SentenceReady,
)


class _ImmediateTTS:
    def __init__(self):
        self.calls: list[str] = []
        self.stop_calls = 0

    def generate_and_play(self, text: str):
        self.calls.append(text)
        return PlaybackResult(PlaybackStatus.COMPLETED)

    def stop_playing(self):
        self.stop_calls += 1


class _BlockingTTS:
    def __init__(self, *, release_on_stop: bool = False, stop_error: bool = False):
        self.calls: list[str] = []
        self.stop_calls = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.release_on_stop = release_on_stop
        self.stop_error = stop_error

    def generate_and_play(self, text: str):
        self.started.set()
        self.release.wait(timeout=5.0)
        self.calls.append(text)
        return PlaybackResult(PlaybackStatus.COMPLETED)

    def stop_playing(self):
        self.stop_calls += 1
        if self.stop_error:
            raise RuntimeError("stop failed")
        if self.release_on_stop:
            self.release.set()


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _blocked_queue(*, release_on_stop: bool = False, stop_error: bool = False):
    controller = GenerationController()
    record = controller.start_generation()
    tts = _BlockingTTS(
        release_on_stop=release_on_stop,
        stop_error=stop_error,
    )
    queue = SentenceDeliveryQueue(controller, tts)
    queue.start()
    assert queue.enqueue(SentenceReady(record.generation_id, 0, "阻塞句。"))
    assert tts.started.wait(timeout=2.0)
    return controller, record, tts, queue


def _release_and_join(tts: _BlockingTTS, thread: threading.Thread | None):
    tts.release.set()
    if thread is not None:
        thread.join(timeout=2.0)
        assert not thread.is_alive()


def test_worker01_start_twice_keeps_one_live_worker():
    controller = GenerationController()
    controller.start_generation()
    queue = SentenceDeliveryQueue(controller, _ImmediateTTS())
    queue.start()
    first = queue._thread
    queue.start()
    assert queue._thread is first
    assert first is not None and first.is_alive()
    queue.shutdown()
    assert first is not None and not first.is_alive()


def test_worker02_timeout_retains_live_worker_reference():
    _, _, tts, queue = _blocked_queue()
    old_thread = queue._thread
    assert old_thread is not None
    queue.shutdown(timeout=0.01)
    assert old_thread.is_alive()
    assert queue._thread is old_thread
    _release_and_join(tts, old_thread)
    queue.shutdown(timeout=2.0)

def test_worker03_start_during_pending_shutdown_does_not_create_worker():
    _, _, tts, queue = _blocked_queue()
    old_thread = queue._thread
    assert old_thread is not None
    queue.shutdown(timeout=0.01)
    queue.start()
    assert queue._thread is old_thread
    assert old_thread.is_alive()
    _release_and_join(tts, old_thread)
    queue.shutdown(timeout=2.0)


def test_worker04_pending_shutdown_keeps_stop_state_set():
    _, _, tts, queue = _blocked_queue()
    old_thread = queue._thread
    assert old_thread is not None
    queue.shutdown(timeout=0.01)
    assert queue._stop.is_set()
    queue.start()
    assert queue._stop.is_set()
    _release_and_join(tts, old_thread)
    queue.shutdown(timeout=2.0)


def test_worker05_releasing_provider_allows_old_worker_to_terminate():
    _, _, tts, queue = _blocked_queue()
    old_thread = queue._thread
    assert old_thread is not None
    queue.shutdown(timeout=0.01)
    _release_and_join(tts, old_thread)
    queue.shutdown(timeout=2.0)
    assert queue._thread is None


def test_worker06_restart_is_allowed_only_after_old_worker_exits():
    controller, _, tts, queue = _blocked_queue()
    old_thread = queue._thread
    assert old_thread is not None
    queue.shutdown(timeout=0.01)
    _release_and_join(tts, old_thread)
    queue.start()
    new_thread = queue._thread
    assert new_thread is not None and new_thread is not old_thread
    assert new_thread.is_alive()
    queue.shutdown(timeout=2.0)


def test_worker07_restart_does_not_consume_a_stale_shutdown_sentinel():
    controller, _, tts, queue = _blocked_queue()
    old_thread = queue._thread
    assert old_thread is not None
    queue.shutdown(timeout=0.01)
    _release_and_join(tts, old_thread)

    new_record = controller.start_generation()
    queue.start()
    assert queue.enqueue(SentenceReady(new_record.generation_id, 0, "新句。"))
    assert _wait_for(lambda: tts.calls == ["阻塞句。", "新句。"])
    queue.shutdown(timeout=2.0)


def test_worker08_pending_shutdown_rejects_new_sentence_work():
    controller, _, tts, queue = _blocked_queue()
    old_thread = queue._thread
    assert old_thread is not None
    queue.shutdown(timeout=0.01)
    new_record = controller.start_generation()
    assert not queue.enqueue(SentenceReady(new_record.generation_id, 0, "不应播。"))
    _release_and_join(tts, old_thread)
    queue.shutdown(timeout=2.0)


def test_worker09_shutdown_requests_best_effort_provider_stop():
    _, _, tts, queue = _blocked_queue()
    old_thread = queue._thread
    assert old_thread is not None
    queue.shutdown(timeout=0.01)
    assert tts.stop_calls == 1
    _release_and_join(tts, old_thread)
    queue.shutdown(timeout=2.0)


def test_worker10_provider_stop_error_does_not_break_shutdown():
    _, _, tts, queue = _blocked_queue(stop_error=True)
    old_thread = queue._thread
    assert old_thread is not None
    queue.shutdown(timeout=0.01)
    assert queue._thread is old_thread
    _release_and_join(tts, old_thread)
    queue.shutdown(timeout=2.0)


def test_worker11_repeated_shutdown_is_idempotent():
    _, _, tts, queue = _blocked_queue()
    old_thread = queue._thread
    assert old_thread is not None
    queue.shutdown(timeout=0.01)
    queue.shutdown(timeout=0.01)
    assert queue._thread is old_thread
    assert tts.stop_calls == 1
    _release_and_join(tts, old_thread)
    queue.shutdown(timeout=2.0)


def test_worker12_shutdown_suppresses_late_audio_finished_callback():
    controller = GenerationController()
    record = controller.start_generation()
    tts = _BlockingTTS()
    events = []
    queue = SentenceDeliveryQueue(controller, tts, on_event=events.append)
    queue.start()
    assert queue.enqueue(SentenceReady(record.generation_id, 0, "不会完成。"))
    assert tts.started.wait(timeout=2.0)
    old_thread = queue._thread
    assert old_thread is not None
    queue.shutdown(timeout=0.01)
    tts.release.set()
    old_thread.join(timeout=2.0)
    assert not old_thread.is_alive()
    assert not any(isinstance(event, AudioFinished) for event in events)
    queue.shutdown(timeout=2.0)
