"""Focused FSMN-VAD adapter and recording endpoint regressions."""

from __future__ import annotations

import ast
import time
from pathlib import Path

import numpy as np
import pytest

from services.fsmn_vad_adapter import FSMNVADAdapter, VadEvent
from services.stt_service import STTService


ROOT = Path(__file__).resolve().parents[1]


class _FakeVadModel:
    def __init__(self, outputs=None):
        self.outputs = list(outputs or [])
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self.outputs.pop(0) if self.outputs else []


def test_adapter_loads_fsmn_vad_and_sends_ordered_200ms_chunks():
    model = _FakeVadModel([[], []])
    factory_calls = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return model

    adapter = FSMNVADAdapter(device="cpu", model_factory=factory)
    assert adapter.load() is True
    assert factory_calls == [
        {"model": "fsmn-vad", "device": "cpu", "disable_update": True}
    ]

    first = np.zeros(3200, dtype=np.float32)
    second = np.ones(3200, dtype=np.float32)
    assert adapter.feed(first) is VadEvent.NONE
    assert adapter.feed(second) is VadEvent.NONE

    assert [len(call["input"]) for call in model.calls] == [3200, 3200]
    assert [call["chunk_size"] for call in model.calls] == [200, 200]
    assert [call["is_final"] for call in model.calls] == [False, False]
    assert model.calls[0]["cache"] is model.calls[1]["cache"]


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ([], VadEvent.NONE),
        ([{"value": [[100, -1]]}], VadEvent.SPEECH_START),
        ([{"value": [[-1, 900]]}], VadEvent.SPEECH_END),
        ([{"value": [[100, 900]]}], VadEvent.SPEECH_END),
    ],
)
def test_adapter_maps_official_streaming_vad_segments(output, expected):
    model = _FakeVadModel([output])
    adapter = FSMNVADAdapter(model_factory=lambda **kwargs: model)
    adapter.load()

    assert adapter.feed(np.zeros(3200, dtype=np.float32)) is expected


def test_initial_end_without_start_is_not_marked_as_speech():
    model = _FakeVadModel([[{"value": [[-1, 900]]}]])
    adapter = FSMNVADAdapter(model_factory=lambda **kwargs: model)
    adapter.load()

    assert adapter.feed(np.zeros(3200, dtype=np.float32)) is VadEvent.SPEECH_END
    assert adapter.speech_seen is False


class _FakeStream:
    def __init__(self, **kwargs):
        self.callback = kwargs["callback"]
        self.stopped = False
        self.closed = False

    def start(self):
        return None

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class _EndpointAdapter:
    def __init__(self, events):
        self.events = list(events)
        self.reset_count = 0
        self.feed_calls = []
        self.speech_seen = False

    def reset(self):
        self.reset_count += 1
        self.speech_seen = False

    def feed(self, chunk, *, is_final=False):
        self.feed_calls.append((len(chunk), is_final))
        event = self.events.pop(0) if self.events else VadEvent.NONE
        if event is VadEvent.SPEECH_START:
            self.speech_seen = True
        return event

    def close(self):
        pass


def _service_with_endpoint_adapter(monkeypatch, adapter):
    stream_holder = {}

    def construct_stream(**kwargs):
        stream = _FakeStream(**kwargs)
        stream_holder["stream"] = stream
        return stream

    monkeypatch.setattr(
        "services.stt_service.sd.query_devices",
        lambda kind=None: [{"name": "Test Microphone", "max_input_channels": 1}],
    )
    monkeypatch.setattr("services.stt_service.sd.InputStream", construct_stream)
    service = STTService()
    service.vad_adapter = adapter
    service._vad_backend = "FSMN_VAD"
    service.set_vad_enabled(True)
    assert service.start_recording() is True
    return service, stream_holder["stream"]


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_fsmn_end_requests_one_stop_and_preserves_final_pcm(monkeypatch):
    adapter = _EndpointAdapter(
        [VadEvent.SPEECH_START, VadEvent.SPEECH_END]
    )
    service, stream = _service_with_endpoint_adapter(monkeypatch, adapter)
    frame = np.ones((1024, 1), dtype=np.float32)
    for _ in range(8):
        stream.callback(frame, 1024, None, None)

    assert _wait_for(service.is_vad_triggered)
    audio = service.stop_recording()

    assert len(audio) == 8192
    assert np.all(audio == 1.0)
    assert stream.stopped and stream.closed
    assert adapter.feed_calls == [(3200, False), (3200, False)]


def test_manual_stop_does_not_wait_for_vad_event_and_keeps_pcm(monkeypatch):
    adapter = _EndpointAdapter([VadEvent.NONE])
    service, stream = _service_with_endpoint_adapter(monkeypatch, adapter)
    frame = np.ones((1024, 1), dtype=np.float32)
    stream.callback(frame, 1024, None, None)

    audio = service.stop_recording()
    assert len(audio) == 1024
    assert service.is_vad_triggered() is False
    assert stream.stopped and stream.closed


def test_initial_fsmn_end_does_not_stop_or_create_empty_turn(monkeypatch):
    adapter = _EndpointAdapter([VadEvent.SPEECH_END])
    service, stream = _service_with_endpoint_adapter(monkeypatch, adapter)
    frame = np.ones((1024, 1), dtype=np.float32)
    for _ in range(4):
        stream.callback(frame, 1024, None, None)

    time.sleep(0.05)
    assert service.is_recording is True
    audio = service.stop_recording()
    assert len(audio) == 4096


def test_fsmn_backend_does_not_run_rms_endpointing_in_callback():
    source = (ROOT / "services" / "stt_service.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    callback = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "audio_callback"
    )
    callback_source = ast.get_source_segment(source, callback) or ""
    assert "VAD_SILENCE_THRESHOLD" not in callback_source
    assert "vad_adapter" not in callback_source


def test_new_recording_resets_vad_adapter(monkeypatch):
    adapter = _EndpointAdapter([])
    service, _stream = _service_with_endpoint_adapter(monkeypatch, adapter)
    service.stop_recording()
    _service_with_endpoint_adapter(monkeypatch, adapter)[0].stop_recording()
    assert adapter.reset_count == 2


def test_stale_vad_event_cannot_stop_new_recording(monkeypatch):
    adapter = _EndpointAdapter([])
    service, old_stream = _service_with_endpoint_adapter(monkeypatch, adapter)
    old_state = service._recording_state
    service.stop_recording()

    service, new_stream = _service_with_endpoint_adapter(monkeypatch, adapter)
    new_state = service._recording_state
    service._handle_vad_event(old_state, VadEvent.SPEECH_END)

    assert service._recording_state is new_state
    assert service.is_recording is True
    assert service.is_vad_triggered() is False
    service.stop_recording()
    assert old_stream.closed and new_stream.closed
