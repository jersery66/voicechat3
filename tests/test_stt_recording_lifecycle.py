"""Deterministic cross-cutting STT recording lifecycle acceptance tests."""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path
import threading

import numpy as np
import pytest

from services import stt_service as stt_module
from services.fsmn_vad_adapter import VadEvent
from services.stt_service import RecordingStartError, STTService


ROOT = Path(__file__).resolve().parents[1]


class _LifecycleStream:
    def __init__(self, *, callback, fail_start=False):
        self.callback = callback
        self.fail_start = fail_start
        self.start_calls = 0
        self.stop_calls = 0
        self.close_calls = 0
        self.started = False
        self.closed = False

    def start(self):
        self.start_calls += 1
        if self.fail_start:
            raise OSError("device rejected start")
        self.started = True

    def stop(self):
        self.stop_calls += 1

    def close(self):
        self.close_calls += 1
        self.closed = True


class _ScriptedVad:
    def __init__(self, events=()):
        self.events = deque(events)
        self.feed_calls = []
        self.reset_calls = 0
        self.speech_seen = False
        self.closed = False

    def reset(self):
        self.reset_calls += 1
        self.speech_seen = False

    def feed(self, chunk, *, is_final=False):
        self.feed_calls.append((np.asarray(chunk).copy(), is_final))
        event = self.events.popleft() if self.events else VadEvent.NONE
        if event is VadEvent.SPEECH_START:
            self.speech_seen = True
        return event

    def close(self):
        self.closed = True


def _patch_microphone(monkeypatch, stream_factory):
    monkeypatch.setattr(
        stt_module.sd,
        "query_devices",
        lambda kind=None: [{"name": "Test Microphone", "max_input_channels": 1}],
    )
    monkeypatch.setattr(stt_module.sd, "InputStream", stream_factory)


def _start_service(monkeypatch, *, vad=None, fail_start=False):
    streams = []

    def construct_stream(**kwargs):
        stream = _LifecycleStream(callback=kwargs["callback"], fail_start=fail_start)
        streams.append(stream)
        return stream

    _patch_microphone(monkeypatch, construct_stream)
    service = STTService()
    if vad is not None:
        service.vad_adapter = vad
        service._vad_backend = "FSMN_VAD"
    else:
        service.set_vad_enabled(False)
    assert service.start_recording() is True
    return service, streams[-1]


def _assert_collector_exited(state):
    collector = state.collector_thread
    assert collector is not None
    collector.join(timeout=2)
    assert not collector.is_alive()


def test_asr01_clean_start_creates_fresh_recording_owners(monkeypatch):
    vad = _ScriptedVad()
    service, first_stream = _start_service(monkeypatch, vad=vad)
    first_state = service._recording_state
    first_queue = first_state.audio_queue
    first_audio = first_state.recorded_audio

    assert first_state.accepting_frames is True
    assert first_state.collector_thread is not None
    assert first_stream.start_calls == 1
    assert service.is_recording is True
    service.stop_recording()
    _assert_collector_exited(first_state)

    service, second_stream = _start_service(monkeypatch, vad=vad)
    second_state = service._recording_state
    assert second_state is not first_state
    assert second_state.audio_queue is not first_queue
    assert second_state.recorded_audio is not first_audio
    assert vad.reset_calls == 2
    assert second_stream.start_calls == 1
    service.stop_recording()
    _assert_collector_exited(second_state)


def test_asr02_start_failure_cleans_resources_without_active_recording(monkeypatch):
    stream = _LifecycleStream(callback=lambda *_args: None, fail_start=True)
    _patch_microphone(monkeypatch, lambda **_kwargs: stream)
    service = STTService()

    with pytest.raises(RecordingStartError):
        service.start_recording()

    state = service._recording_state
    assert stream.start_calls == 1
    assert stream.closed is True
    assert service.is_recording is False
    assert service.stream is None
    assert state is None


def test_asr03_final_callback_frame_is_preserved_in_order(monkeypatch):
    service, stream = _start_service(monkeypatch)
    frames = [
        np.full((1024, 1), value, dtype=np.float32)
        for value in (1.0, 2.0, 3.0)
    ]
    for frame in frames:
        stream.callback(frame, len(frame), None, None)

    audio = service.stop_recording()
    expected = np.concatenate(frames, axis=0).reshape(-1)
    np.testing.assert_array_equal(audio, expected)


def test_asr04_manual_stop_uses_one_sentinel_and_drains_queue(monkeypatch):
    service, stream = _start_service(monkeypatch)
    state = service._recording_state
    sentinel_count = []
    original_put = state.audio_queue.put

    def counting_put(item, *args, **kwargs):
        if item is stt_module._RECORDING_SENTINEL:
            sentinel_count.append(item)
        return original_put(item, *args, **kwargs)

    state.audio_queue.put = counting_put
    frame = np.full((1024, 1), 7.0, dtype=np.float32)
    for _ in range(3):
        stream.callback(frame, len(frame), None, None)

    audio = service.stop_recording()
    assert len(sentinel_count) == 1
    assert state.sentinel_enqueued is True
    assert state.audio_queue.empty()
    np.testing.assert_array_equal(audio, np.full(3072, 7.0, dtype=np.float32))
    _assert_collector_exited(state)


def test_asr05_stop_and_cleanup_are_idempotent(monkeypatch):
    service, stream = _start_service(monkeypatch)
    state = service._recording_state
    stream.callback(np.ones((1024, 1), dtype=np.float32), 1024, None, None)

    first_audio = service.stop_recording()
    second_audio = service.stop_recording()
    service.cleanup()
    service.cleanup()

    assert len(first_audio) == 1024
    assert len(second_audio) == 0
    assert stream.close_calls == 1
    _assert_collector_exited(state)


def test_asr06_fsmn_endpoint_stops_once_and_repeated_end_is_stale(monkeypatch):
    vad = _ScriptedVad(
        [
            VadEvent.NONE,
            VadEvent.SPEECH_START,
            VadEvent.NONE,
            VadEvent.SPEECH_END,
        ]
    )
    service, stream = _start_service(monkeypatch, vad=vad)
    state = service._recording_state
    stop_results = []
    stop_seen = threading.Event()
    original_request = service._request_recording_stop

    def request_stop(*args, **kwargs):
        result = original_request(*args, **kwargs)
        stop_results.append((kwargs.get("vad_triggered", False), result))
        if kwargs.get("vad_triggered") and result:
            stop_seen.set()
        return result

    service._request_recording_stop = request_stop
    frame = np.ones((1024, 1), dtype=np.float32)
    for _ in range(13):
        stream.callback(frame, len(frame), None, None)

    assert stop_seen.wait(timeout=2)
    service.stop_recording()
    service._handle_vad_event(state, VadEvent.SPEECH_END)

    assert sum(result for _is_vad, result in stop_results) == 1
    assert [len(chunk) for chunk, _final in vad.feed_calls] == [3200] * 4
    _assert_collector_exited(state)


def test_asr07_fsmn_is_single_endpoint_owner_and_rms_is_fallback_only(monkeypatch):
    vad = _ScriptedVad()
    service, stream = _start_service(monkeypatch, vad=vad)
    state = service._recording_state
    rms_calls = []

    def rms_should_not_run(*_args, **_kwargs):
        rms_calls.append(True)
        raise AssertionError("RMS endpointing ran while FSMN-VAD was active")

    monkeypatch.setattr(service, "_update_rms_vad", rms_should_not_run)
    frame = np.ones((1024, 1), dtype=np.float32)
    for _ in range(4):
        stream.callback(frame, len(frame), None, None)
    state.audio_queue.join()
    service.stop_recording()

    assert len(vad.feed_calls) == 1
    assert rms_calls == []
    assert service._vad_backend == "FSMN_VAD"


def test_asr07_failed_fsmn_load_selects_only_rms_fallback(monkeypatch):
    class FailingVad:
        def __init__(self, **_kwargs):
            pass

        def load(self):
            raise RuntimeError("checkpoint unavailable")

        def close(self):
            pass

    monkeypatch.setattr(stt_module, "FSMNVADAdapter", FailingVad)
    service = STTService()
    service.model = object()

    service._ensure_fsmn_vad()

    assert service.vad_adapter is None
    assert service._vad_backend == "RMS_FALLBACK"
    assert service._vad_adapter_attempted is True


def test_asr08_completed_utterance_calls_final_asr_once_without_partial_path(monkeypatch):
    service, stream = _start_service(monkeypatch)
    frame = np.ones((1024, 1), dtype=np.float32)
    stream.callback(frame, len(frame), None, None)
    calls = []

    def final_transcribe(audio):
        calls.append(np.asarray(audio).copy())
        return "最终转写"

    monkeypatch.setattr(service, "transcribe", final_transcribe)
    assert service.record_and_transcribe() == "最终转写"
    assert len(calls) == 1
    assert len(calls[0]) == 1024

    source = (ROOT / "services" / "stt_service.py").read_text(encoding="utf-8")
    process_source = source[source.index("def _process_vad_audio"):source.index("def _request_recording_stop")]
    assert ".transcribe(" not in process_source


@pytest.mark.parametrize("model_behavior", ["empty", "malformed", "error"])
def test_asr09_current_final_asr_outcomes_are_deterministic(model_behavior):
    class Model:
        def inference(self, **_kwargs):
            if model_behavior == "empty":
                return []
            if model_behavior == "malformed":
                return [{"unexpected": "shape"}]
            raise RuntimeError("ASR provider failure")

    service = STTService()
    service.model = Model()
    if model_behavior == "empty":
        audio = np.array([], dtype=np.float32)
    else:
        audio = np.ones(3200, dtype=np.float32)

    # Current public behavior intentionally remains an empty final transcript
    # for all three no-usable-text/provider-failure outcomes; this test records
    # that fact without redesigning the final recognizer in a lifecycle commit.
    assert service.transcribe(audio) == ""


def test_asr10_lifecycle_change_does_not_add_hotwords_or_text_rewrites():
    source = (ROOT / "services" / "stt_service.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    lifecycle_names = {
        "start_recording",
        "_process_vad_audio",
        "_process_fsmn_vad_audio",
        "_handle_vad_event",
        "_request_recording_stop",
    }
    methods = {
        node.name: ast.get_source_segment(source, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in lifecycle_names
    }
    assert "hotword" not in "\n".join(methods.values()).lower()
    assert "_correct_common_errors" not in "\n".join(methods.values())


def test_asr14_fsmn_manual_stop_and_cleanup_share_one_close_transition(monkeypatch):
    vad = _ScriptedVad([VadEvent.SPEECH_START])
    service, stream = _start_service(monkeypatch, vad=vad)
    state = service._recording_state
    service._handle_vad_event(state, VadEvent.SPEECH_START)
    service._handle_vad_event(state, VadEvent.SPEECH_END)
    service.stop_recording()
    service.cleanup()

    assert stream.close_calls == 1
    _assert_collector_exited(state)


def test_asr15_rapid_restart_isolates_old_state_and_vad_event(monkeypatch):
    vad = _ScriptedVad()
    service, old_stream = _start_service(monkeypatch, vad=vad)
    old_state = service._recording_state
    old_stream.callback(np.full((1024, 1), 1.0, dtype=np.float32), 1024, None, None)
    old_audio = service.stop_recording()
    _assert_collector_exited(old_state)

    service, new_stream = _start_service(monkeypatch, vad=vad)
    new_state = service._recording_state
    new_stream.callback(np.full((1024, 1), 2.0, dtype=np.float32), 1024, None, None)
    service._handle_vad_event(old_state, VadEvent.SPEECH_END)

    assert service._recording_state is new_state
    assert service.is_recording is True
    assert service.is_vad_triggered() is False
    new_audio = service.stop_recording()

    np.testing.assert_array_equal(old_audio, np.ones(1024, dtype=np.float32))
    np.testing.assert_array_equal(new_audio, np.full(1024, 2.0, dtype=np.float32))
    assert old_stream.closed and new_stream.closed
    assert vad.reset_calls == 2
    _assert_collector_exited(new_state)


def test_asr13_stop_during_device_open_does_not_allow_late_success(monkeypatch):
    open_entered = threading.Event()
    allow_open = threading.Event()
    stream_holder = []

    def construct_blocking_stream(**kwargs):
        open_entered.set()
        assert allow_open.wait(timeout=2), "test did not release blocked device open"
        stream = _LifecycleStream(callback=kwargs["callback"])
        stream_holder.append(stream)
        return stream

    _patch_microphone(monkeypatch, construct_blocking_stream)
    service = STTService()
    result = {}
    start_thread = threading.Thread(
        target=lambda: _capture_start_result(service, result),
        daemon=True,
    )
    start_thread.start()
    assert open_entered.wait(timeout=2)
    cancelled_state = service._recording_state
    assert cancelled_state is not None

    service.stop_recording()
    allow_open.set()
    start_thread.join(timeout=2)
    assert not start_thread.is_alive()
    if cancelled_state.collector_thread is not None:
        cancelled_state.collector_thread.join(timeout=2)

    # Cancellation is an ordinary lifecycle outcome, not a microphone error.
    assert result.get("returned") is False
    assert result.get("error") is None
    assert service.is_recording is False
    assert service.stream is None
    if stream_holder:
        assert stream_holder[0].started is False
        assert stream_holder[0].closed is True


def _capture_start_result(service, result):
    try:
        result["returned"] = service.start_recording()
    except Exception as exc:  # pragma: no cover - assertion reports the value
        result["error"] = exc
