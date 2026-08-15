"""Focused regressions for microphone startup failure propagation."""

from __future__ import annotations

import ast
import threading
from pathlib import Path

import pytest

from services import stt_service as stt_module
from services.stt_service import RecordingStartError, STTService


ROOT = Path(__file__).resolve().parents[1]


def _patch_microphone(monkeypatch, *, stream_factory):
    monkeypatch.setattr(
        stt_module.sd,
        "query_devices",
        lambda kind=None: [{"name": "Test Microphone", "max_input_channels": 1}],
    )
    monkeypatch.setattr(stt_module.sd, "InputStream", stream_factory)


class _StartedStream:
    def __init__(self, **_kwargs):
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


def test_successful_start_returns_true_and_keeps_recording_active(monkeypatch):
    stream = _StartedStream()
    _patch_microphone(monkeypatch, stream_factory=lambda **kwargs: stream)

    service = STTService()
    assert service.start_recording() is True
    assert stream.started is True
    assert service.is_recording is True
    assert service._recording_state is not None

    service.stop_recording()
    assert stream.stopped is True
    assert stream.closed is True
    assert service.is_recording is False


def test_no_microphone_raises_recording_start_error_and_clears_state(monkeypatch):
    monkeypatch.setattr(stt_module.sd, "query_devices", lambda kind=None: [])

    service = STTService()
    with pytest.raises(RecordingStartError):
        service.start_recording()

    assert service.is_recording is False
    assert service.stream is None
    assert service._recording_state is None


def test_device_query_failure_raises_recording_start_error(monkeypatch):
    def query_devices(kind=None):
        raise OSError("PortAudio unavailable")

    monkeypatch.setattr(stt_module.sd, "query_devices", query_devices)
    service = STTService()

    with pytest.raises(RecordingStartError) as caught:
        service.start_recording()

    assert "PortAudio unavailable" in str(caught.value)
    assert service.is_recording is False
    assert service._recording_state is None


def test_input_stream_constructor_failure_raises_and_cleans(monkeypatch):
    def construct_stream(**_kwargs):
        raise OSError("cannot open input stream")

    _patch_microphone(monkeypatch, stream_factory=construct_stream)
    service = STTService()

    with pytest.raises(RecordingStartError) as caught:
        service.start_recording()

    assert "cannot open input stream" in str(caught.value)
    assert service.is_recording is False
    assert service.stream is None
    assert service._recording_state is None


def test_stream_start_failure_raises_and_closes_stream(monkeypatch):
    class FailingStream(_StartedStream):
        def start(self):
            raise OSError("device rejected start")

    stream = FailingStream()
    _patch_microphone(monkeypatch, stream_factory=lambda **kwargs: stream)
    service = STTService()

    with pytest.raises(RecordingStartError) as caught:
        service.start_recording()

    assert "device rejected start" in str(caught.value)
    assert stream.stopped is True
    assert stream.closed is True
    assert service.is_recording is False
    assert service.stream is None
    assert service._recording_state is None


def test_main_window_uses_attempt_correlated_failure_event_without_pipeline_start():
    source = (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    methods = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name in {
            "_on_record_started",
            "_on_record_stopped",
            "_start_recording_worker",
            "_handle_recording_start_failed",
            "process_queue",
        }
    }

    assert set(methods) == {
        "_on_record_started",
        "_on_record_stopped",
        "_start_recording_worker",
        "_handle_recording_start_failed",
        "process_queue",
    }
    start_source = ast.get_source_segment(source, methods["_on_record_started"]) or ""
    worker_source = ast.get_source_segment(source, methods["_start_recording_worker"]) or ""
    failure_source = ast.get_source_segment(
        source, methods["_handle_recording_start_failed"]
    ) or ""
    queue_source = ast.get_source_segment(source, methods["process_queue"]) or ""

    assert "_recording_attempt_id" in start_source
    assert "_active_recording_attempt_id" in start_source
    assert "_start_recording_worker" in start_source
    assert "threading.Thread(target=self.stt_service.start_recording" not in start_source
    assert "recording_start_failed" in worker_source
    assert "attempt_id" in worker_source
    assert "_active_recording_attempt_id" in failure_source
    assert "reset_recording" in failure_source
    assert "_run_pipeline" not in failure_source
    assert "recording_start_failed" in queue_source


def test_cancelled_start_during_input_stream_open_returns_false_and_closes_candidate(
    monkeypatch,
):
    opened = threading.Event()
    release = threading.Event()
    candidates = []

    class BlockingStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.started = False
            self.closed = False

        def start(self):
            self.started = True

        def stop(self):
            pass

        def close(self):
            self.closed = True

    def construct_stream(**kwargs):
        opened.set()
        assert release.wait(timeout=2), "test did not release blocked stream open"
        stream = BlockingStream(**kwargs)
        candidates.append(stream)
        return stream

    _patch_microphone(monkeypatch, stream_factory=construct_stream)
    service = STTService()
    result = {}

    def start_attempt():
        try:
            result["returned"] = service.start_recording()
        except Exception as exc:  # pragma: no cover - assertion reports value
            result["error"] = exc

    thread = threading.Thread(target=start_attempt, daemon=True)
    thread.start()
    assert opened.wait(timeout=2)
    cancelled_state = service._recording_state
    assert cancelled_state is not None

    service.stop_recording()
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()

    assert result == {"returned": False}
    assert candidates and candidates[0].started is False
    assert candidates[0].closed is True
    assert cancelled_state.collector_thread is None
    assert service._recording_state is None
    assert service.stream is None
    assert service.is_recording is False
