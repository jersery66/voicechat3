"""Frozen deterministic acceptance tests for the final TTS hardening gate.

This file is intentionally test-only.  It exercises the contracts in
``asr_tts_hardening_spec.md`` that are not fully covered by the focused
completion-status and VoxCPM cancellation regressions.  A red test is a
production defect and must not be weakened or xfailed in this diagnostic step.
"""

from __future__ import annotations

import ast
from pathlib import Path
import queue
import threading
import time

import numpy as np
import pytest

from adapters.tts_results import PlaybackResult, PlaybackStatus
from conversation.delivery import (
    AudioFinished,
    AudioStarted,
    DeliveryLedger,
    GenerationController,
    SentenceDeliveryQueue,
    SentenceReady,
)
from services.pipeline import ConversationPipeline, PipelineConfig
from services.tts_service_voxcpm import TTSService


ROOT = Path(__file__).resolve().parents[1]


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class _ImmediateTTS:
    def __init__(self, result=PlaybackResult(PlaybackStatus.COMPLETED)):
        self.result = result
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0
        self.stop_calls = 0
        self._lock = threading.Lock()

    def generate_and_play(self, text: str):
        with self._lock:
            self.calls.append(text)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if isinstance(self.result, BaseException):
                raise self.result
            return self.result
        finally:
            with self._lock:
                self.active -= 1

    def stop_playing(self):
        self.stop_calls += 1


class _BlockingTTS(_ImmediateTTS):
    def __init__(self, result=PlaybackResult(PlaybackStatus.COMPLETED)):
        super().__init__(result)
        self.started = threading.Event()
        self.release = threading.Event()

    def generate_and_play(self, text: str):
        with self._lock:
            self.calls.append(text)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            self.release.wait(timeout=3.0)
            if isinstance(self.result, BaseException):
                raise self.result
            return self.result
        finally:
            with self._lock:
                self.active -= 1

    def stop_playing(self):
        self.stop_calls += 1
        self.release.set()


class _VoxOutputStream:
    """Small explicit-stream fake that repeatedly drives the callback."""

    instances: list["_VoxOutputStream"] = []

    def __init__(self, *, callback, **_kwargs):
        self.callback = callback
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.abort_calls = 0
        self.entered = threading.Event()
        type(self).instances.append(self)

    def __enter__(self):
        self.entered.set()

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

    def abort(self):
        self.abort_calls += 1
        self._stop.set()

    def close(self):
        self._stop.set()


class _VoxWav:
    def squeeze(self, _axis):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return np.ones(16, dtype=np.float32)


class _VoxFallbackModel:
    def __init__(self, *, failure: Exception | None = None, block: bool = False):
        self.failure = failure
        self.block = block
        self.started = threading.Event()
        self.release = threading.Event()

    def generate_streaming(self, **_kwargs):
        self.started.set()
        if self.failure is not None:
            raise self.failure
        yield np.ones(16, dtype=np.float32)
        if self.block:
            self.release.wait(timeout=3.0)
        yield np.ones(16, dtype=np.float32)


class _VoxPromptCacheModel:
    def __init__(self, *, failure: Exception | None = None, block: bool = False):
        self.failure = failure
        self.block = block
        self.started = threading.Event()
        self.release = threading.Event()
        self.tts_model = self

    def _generate_with_prompt_cache(self, **_kwargs):
        self.started.set()
        if self.failure is not None:
            raise self.failure
        yield _VoxWav(), None, None
        if self.block:
            self.release.wait(timeout=3.0)
        yield _VoxWav(), None, None


def _vox_service(monkeypatch, model, *, prompt_cache=False):
    service = TTSService.__new__(TTSService)
    service.model = model
    service.sample_rate = 10
    service.prompt_cache = object() if prompt_cache else None
    service.is_playing = False
    service._play_lock = threading.Lock()
    service._active_playback = None
    service._active_playback_lock = threading.RLock()
    _VoxOutputStream.instances.clear()
    monkeypatch.setattr("services.tts_service_voxcpm.sd.OutputStream", _VoxOutputStream)
    monkeypatch.setattr("services.tts_service_voxcpm.sd.stop", lambda: None)
    return service


def _run_vox(service, text="你好。"):
    holder: list[PlaybackResult] = []
    worker = threading.Thread(
        target=lambda: holder.append(service._generate_and_play_inner(text)),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=4.0)
    assert not worker.is_alive()
    return holder[0]


def test_tts01_completed_once_for_one_sentence():
    controller = GenerationController()
    record = controller.start_generation()
    events = []
    tts = _ImmediateTTS()
    delivery = SentenceDeliveryQueue(controller, tts, on_event=events.append)
    delivery.start()
    assert delivery.enqueue(SentenceReady(record.generation_id, 0, "一句完整的话。"))
    assert _wait_for(lambda: len(tts.calls) == 1)
    delivery.shutdown()

    assert tts.calls == ["一句完整的话。"]
    assert [type(event) for event in events] == [AudioStarted, AudioFinished]
    assert events[-1].status is PlaybackStatus.COMPLETED


def test_tts02_provider_exception_is_failed_not_completed():
    controller = GenerationController()
    record = controller.start_generation()
    events = []
    delivery = SentenceDeliveryQueue(
        controller,
        _ImmediateTTS(RuntimeError("provider failed")),
        on_event=events.append,
    )
    delivery.start()
    assert delivery.enqueue(SentenceReady(record.generation_id, 0, "失败句。"))
    assert _wait_for(lambda: any(isinstance(e, AudioFinished) for e in events))
    delivery.shutdown()

    finished = [e for e in events if isinstance(e, AudioFinished)]
    assert len(finished) == 1
    assert finished[0].status is PlaybackStatus.FAILED


def test_tts03_empty_normalized_text_is_failed(monkeypatch):
    service = _vox_service(monkeypatch, _VoxFallbackModel())
    result = service._generate_and_play_inner("[END_SESSION]")
    assert result.status is PlaybackStatus.FAILED
    assert result.error == "empty_text"
    assert not _VoxOutputStream.instances


def test_tts04_cross_layer_cancel_stops_active_and_discards_pending():
    controller = GenerationController()
    record = controller.start_generation()
    tts = _BlockingTTS()
    events = []
    delivery = SentenceDeliveryQueue(controller, tts, on_event=events.append)
    delivery.start()
    assert delivery.enqueue(SentenceReady(record.generation_id, 0, "正在播放。"))
    assert delivery.enqueue(SentenceReady(record.generation_id, 1, "不应播放。"))
    assert tts.started.wait(timeout=2.0)

    assert controller.cancel_generation(record.generation_id, reason="new turn")
    assert tts.stop_calls == 1
    assert tts.release.is_set()
    assert _wait_for(lambda: not tts.active)
    delivery.shutdown()

    assert tts.calls == ["正在播放。"]
    assert not any(isinstance(e, AudioFinished) for e in events)


def test_tts05_stale_completion_cannot_write_history_or_data():
    class Owner:
        conversation_history = [{"role": "user", "content": "你好"}]

    class Data:
        def __init__(self):
            self.calls = []

        def save_assistant_message(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    controller = GenerationController()
    old = controller.start_generation()
    ledger = DeliveryLedger(controller)
    owner = Owner()
    data = Data()
    controller.start_generation()

    assert not ledger.commit_visible(SentenceReady(old.generation_id, 0, "迟到文本。"))
    assert ledger.finalize_history(old.generation_id, owner, data) == ""
    assert owner.conversation_history == [{"role": "user", "content": "你好"}]
    assert data.calls == []


def test_tts06_single_worker_preserves_order_and_excludes_concurrency():
    controller = GenerationController()
    record = controller.start_generation()
    tts = _ImmediateTTS()
    delivery = SentenceDeliveryQueue(controller, tts)
    delivery.start()
    for seq in range(3):
        assert delivery.enqueue(SentenceReady(record.generation_id, seq, f"句子{seq}。"))
    assert _wait_for(lambda: len(tts.calls) == 3)
    delivery.shutdown()

    assert tts.calls == ["句子0。", "句子1。", "句子2。"]
    assert tts.max_active == 1
    assert delivery.worker_count == 1


def test_tts07_failed_audio_preserves_visible_history_once():
    class Owner:
        conversation_history = [{"role": "user", "content": "你好"}]

    class Data:
        def __init__(self):
            self.calls = []

        def save_assistant_message(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    controller = GenerationController()
    record = controller.start_generation()
    ledger = DeliveryLedger(controller)
    visible = SentenceReady(record.generation_id, 0, "已经显示。")
    assert ledger.commit_visible(visible)
    tts = _ImmediateTTS(PlaybackResult(PlaybackStatus.FAILED, "audio error"))
    delivery = SentenceDeliveryQueue(controller, tts)
    delivery.start()
    assert delivery.enqueue(visible)
    assert _wait_for(lambda: len(tts.calls) == 1)
    delivery.shutdown()

    owner = Owner()
    data = Data()
    assert ledger.finalize_history(record.generation_id, owner, data) == "已经显示。"
    assert ledger.finalize_history(record.generation_id, owner, data) == "已经显示。"
    assert owner.conversation_history == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "已经显示。"},
    ]
    assert len(data.calls) == 1


def test_tts08_repeated_stop_cleanup_is_idempotent(monkeypatch):
    model = _VoxFallbackModel(block=True)
    service = _vox_service(monkeypatch, model)
    result_holder: list[PlaybackResult] = []
    worker = threading.Thread(
        target=lambda: result_holder.append(service._generate_and_play_inner("你好。")),
        daemon=True,
    )
    worker.start()
    assert model.started.wait(timeout=2.0)
    assert _wait_for(lambda: bool(_VoxOutputStream.instances))
    stream = _VoxOutputStream.instances[0]
    service.stop_playing()
    service.stop_playing()
    model.release.set()
    worker.join(timeout=4.0)

    assert not worker.is_alive()
    assert stream.abort_calls == 1
    assert result_holder[0].status is PlaybackStatus.CANCELLED


@pytest.mark.parametrize("prompt_cache", [False, True], ids=["public", "prompt-cache"])
def test_tts09_voxcpm_paths_complete_with_deterministic_fakes(monkeypatch, prompt_cache):
    model = _VoxPromptCacheModel() if prompt_cache else _VoxFallbackModel()
    service = _vox_service(monkeypatch, model, prompt_cache=prompt_cache)
    result = _run_vox(service)
    assert result.status is PlaybackStatus.COMPLETED


@pytest.mark.parametrize("prompt_cache", [False, True], ids=["public", "prompt-cache"])
def test_tts09_voxcpm_paths_cancel_during_generation(monkeypatch, prompt_cache):
    model = _VoxPromptCacheModel(block=True) if prompt_cache else _VoxFallbackModel(block=True)
    service = _vox_service(monkeypatch, model, prompt_cache=prompt_cache)
    holder: list[PlaybackResult] = []
    worker = threading.Thread(
        target=lambda: holder.append(service._generate_and_play_inner("你好。")),
        daemon=True,
    )
    worker.start()
    assert model.started.wait(timeout=2.0)
    service.stop_playing()
    model.release.set()
    worker.join(timeout=4.0)
    assert not worker.is_alive()
    assert holder[0].status is PlaybackStatus.CANCELLED


@pytest.mark.parametrize("prompt_cache", [False, True], ids=["public", "prompt-cache"])
def test_tts09_voxcpm_paths_propagate_provider_failure(monkeypatch, prompt_cache):
    error = RuntimeError("synthetic provider failure")
    model = _VoxPromptCacheModel(failure=error) if prompt_cache else _VoxFallbackModel(failure=error)
    service = _vox_service(monkeypatch, model, prompt_cache=prompt_cache)
    result = _run_vox(service)
    assert result.status is PlaybackStatus.FAILED
    assert result.error.startswith("generation_error")


def test_tts10_production_selector_is_voxcpm_only():
    selector_source = (ROOT / "services" / "tts_service.py").read_text(encoding="utf-8")
    assert "services.tts_service_voxcpm" in selector_source
    assert "tts_service_cosyvoice" not in selector_source


def test_tts11_preflight_and_cleanup_contracts_remain_available(monkeypatch):
    class Properties:
        total_memory = 6 * 1024**3

    monkeypatch.setattr("services.tts_service_voxcpm.torch.cuda.is_available", lambda: True)
    monkeypatch.setattr(
        "services.tts_service_voxcpm.torch.cuda.get_device_properties",
        lambda _index: Properties(),
    )
    assert TTSService.get_load_blocker() is not None

    service = TTSService.__new__(TTSService)
    service._active_playback = None
    service._active_playback_lock = threading.RLock()
    service.is_playing = False
    unloaded = []
    monkeypatch.setattr(service, "unload_model", lambda: unloaded.append(True))
    service.cleanup()
    assert unloaded == [True]


@pytest.mark.parametrize("warmup_mode", ["empty", "error"])
def test_tts12_warmup_failure_never_reports_ready(warmup_mode):
    service = TTSService.__new__(TTSService)
    if warmup_mode == "empty":
        service.generate = lambda _text: np.array([], dtype=np.float32)
    else:
        def failing_generate(_text):
            raise RuntimeError("warmup failed")
        service.generate = failing_generate
    assert service.warmup() is False


class _DeferredOverflowStream:
    def __init__(self, *, callback, finished: threading.Event, outputs: list[np.ndarray], **_kwargs):
        self.callback = callback
        self.finished = finished
        self.outputs = outputs
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def __enter__(self):
        def consume_after_generation():
            self.finished.wait(timeout=3.0)
            while not self.stop.is_set():
                outdata = np.zeros((64, 1), dtype=np.float32)
                try:
                    self.callback(outdata, 64, None, None)
                except Exception:
                    break
                self.outputs.append(outdata.copy())
                time.sleep(0.001)

        self.thread = threading.Thread(target=consume_after_generation, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1.0)
        return False

    def abort(self):
        self.stop.set()

    def close(self):
        self.stop.set()


def test_tts13_ring_buffer_rejects_overflow_without_overwriting_unread_audio(monkeypatch):
    generation_finished = threading.Event()
    outputs: list[np.ndarray] = []

    class OverflowModel:
        def generate_streaming(self, **_kwargs):
            yield np.ones(800, dtype=np.float32)
            yield np.full(800, 2.0, dtype=np.float32)
            generation_finished.set()

    def stream_factory(*, callback, **kwargs):
        return _DeferredOverflowStream(
            callback=callback,
            finished=generation_finished,
            outputs=outputs,
            **kwargs,
        )

    service = _vox_service(monkeypatch, OverflowModel())
    monkeypatch.setattr("services.tts_service_voxcpm.sd.OutputStream", stream_factory)
    result = _run_vox(service)

    assert result.status is PlaybackStatus.COMPLETED
    assert outputs
    # The first unread samples must be the first producer chunk.  Current
    # production ring arithmetic wraps and returns the second chunk instead.
    assert np.all(outputs[0][:, 0] == 1.0)


def test_tts14_cosyvoice_cancel_does_not_flush_buffered_chunks():
    from services.tts_service_cosyvoice import TTSService as CosyTTSService

    class Stream:
        def __init__(self):
            self.writes = []

        def is_active(self):
            return True

        def write(self, data):
            self.writes.append(data)

    playback_queue = queue.Queue()
    stop_event = threading.Event()
    stream = Stream()
    playback_queue.put(np.ones(8, dtype=np.float32))
    # Let the worker consume one chunk into its pre-buffer before cancellation
    # so the post-loop flush path is exercised deterministically.
    worker_started = threading.Event()

    original_worker = CosyTTSService._playback_worker

    def instrumented_worker(self, playback_queue, stream, stop_event, pre_buffer=5):
        worker_started.set()
        return original_worker(self, playback_queue, stream, stop_event, pre_buffer)

    service = CosyTTSService.__new__(CosyTTSService)
    service._playback_worker = instrumented_worker.__get__(service, CosyTTSService)
    worker = threading.Thread(
        target=service._playback_worker,
        args=(playback_queue, stream, stop_event),
        kwargs={"pre_buffer": 3},
        daemon=True,
    )
    worker.start()
    assert worker_started.wait(timeout=1.0)
    assert _wait_for(playback_queue.empty, timeout=1.0)
    stop_event.set()
    playback_queue.put(None)
    worker.join(timeout=2.0)
    assert not worker.is_alive()
    assert stream.writes == []


def test_tts14_cosyvoice_missing_prompt_is_contained():
    from services.tts_service_cosyvoice import TTSService as CosyTTSService

    service = CosyTTSService.__new__(CosyTTSService)
    service._use_cached_speaker = False
    service.prompt_wav = "missing-prompt.wav"
    service.prompt_text = ""
    service.model = object()
    assert service._build_synthesis_kwargs("一句话。", stream=True) is None


def test_tts15_queue_timeout_cannot_start_a_second_live_worker():
    controller = GenerationController()
    record = controller.start_generation()
    tts = _BlockingTTS()
    delivery = SentenceDeliveryQueue(controller, tts)
    delivery.start()
    old_thread = delivery._thread
    assert old_thread is not None
    assert delivery.enqueue(SentenceReady(record.generation_id, 0, "阻塞句。"))
    assert tts.started.wait(timeout=2.0)

    delivery.shutdown(timeout=0.01)
    assert old_thread.is_alive()
    delivery.start()
    assert delivery._thread is old_thread

    tts.release.set()
    old_thread.join(timeout=2.0)
    delivery.shutdown(timeout=2.0)
    assert not old_thread.is_alive()


def test_tts16_pipeline_use_tts_false_makes_zero_provider_calls():
    from tests.integration.fakes import FakeAgent, FakeData, FakeLLM, FakeRAG, FakeReport, FakeTTS

    tts = FakeTTS()
    pipeline = ConversationPipeline(
        stt_service=None,
        llm_service=FakeLLM(),
        tts_service=tts,
        rag_service=FakeRAG(),
        agent_service=FakeAgent(),
        report_service=FakeReport(),
        data_manager=FakeData(),
        session_emotions=[],
    )
    try:
        pipeline.execute(
            PipelineConfig(use_stt=False, use_tts=False, user_text="你好"),
            lambda *_args: None,
        )
    finally:
        pipeline.shutdown()
    assert tts.played == []


def test_tts17_stale_enqueue_barrier_has_no_audio_or_history_side_effect():
    class Owner:
        conversation_history = []

    class Data:
        def __init__(self):
            self.calls = []

        def save_assistant_message(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    controller = GenerationController()
    old = controller.start_generation()
    tts = _ImmediateTTS()
    delivery = SentenceDeliveryQueue(controller, tts)
    ledger = DeliveryLedger(controller)
    controller.start_generation()
    stale = SentenceReady(old.generation_id, 0, "不能提交。")
    assert not delivery.enqueue(stale)
    assert not ledger.commit_visible(stale)
    assert ledger.finalize_history(old.generation_id, Owner(), Data()) == ""
    assert tts.calls == []


def test_tts18_new_user_turn_cancels_old_participant_audio():
    controller = GenerationController()
    old = controller.start_generation()
    tts = _BlockingTTS()
    delivery = SentenceDeliveryQueue(controller, tts)
    delivery.start()
    assert delivery.enqueue(SentenceReady(old.generation_id, 0, "旧一轮。"))
    assert tts.started.wait(timeout=2.0)

    new = controller.start_generation()
    assert new.generation_id != old.generation_id
    assert tts.stop_calls == 1
    assert tts.release.is_set()
    delivery.shutdown(timeout=2.0)


def test_tts19_provider_stall_triggers_bounded_sentence_flush():
    from tests.integration.fakes import FakeAgent, FakeData, FakeRAG, FakeReport, FakeTTS

    class StalledLLM:
        conversation_history = []

        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def chat(self, _text, system_suffix="", *, commit_history=False):
            self.started.set()
            yield "这是已经稳定但没有句号的文本"
            self.release.wait(timeout=3.0)

    llm = StalledLLM()
    pipeline = ConversationPipeline(
        stt_service=None,
        llm_service=llm,
        tts_service=FakeTTS(),
        rag_service=FakeRAG(),
        agent_service=FakeAgent(),
        report_service=FakeReport(),
        data_manager=FakeData(),
        session_emotions=[],
    )
    record = pipeline.delivery_controller.start_generation()
    events = []
    worker = threading.Thread(
        target=lambda: pipeline._stream_llm(
            "你好", "", lambda kind, content: events.append((kind, content)),
            generation_id=record.generation_id,
        ),
        daemon=True,
    )
    worker.start()
    assert llm.started.wait(timeout=2.0)
    try:
        assert _wait_for(
            lambda: any(isinstance(content, SentenceReady) for _, content in events),
            timeout=1.2,
        )
    finally:
        llm.release.set()
        worker.join(timeout=4.0)
        pipeline.shutdown()


def test_tts20_generation_records_have_a_finite_retention_bound():
    controller = GenerationController()
    retention = getattr(controller, "max_records", None)
    assert isinstance(retention, int) and retention > 0
    for _ in range(retention * 4):
        controller.start_generation()
    assert len(controller._records) <= retention


def test_tts20_sentence_queue_has_a_finite_capacity():
    controller = GenerationController()
    queue_owner = SentenceDeliveryQueue(controller, _ImmediateTTS())
    assert queue_owner._queue.maxsize > 0


def test_tts21_audio_status_is_delivery_telemetry_only():
    source = (ROOT / "conversation" / "delivery.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "conversation.turn_policy" not in imported_modules
    assert "app.engine" not in imported_modules
    assert "core.scale_fsm" not in imported_modules


def test_x01_authority_modules_remain_separate_from_tts_delivery():
    assert "TurnPolicy" in (ROOT / "conversation" / "turn_policy.py").read_text(encoding="utf-8")
    assert "ScaleRuntime" in (ROOT / "core" / "scale_fsm.py").read_text(encoding="utf-8")
    assert "SessionEngine" in (ROOT / "app" / "engine.py").read_text(encoding="utf-8")
    delivery_source = (ROOT / "conversation" / "delivery.py").read_text(encoding="utf-8")
    assert "TurnDecision" not in delivery_source


def test_x02_stale_tts_callback_cannot_mutate_scale_or_lifecycle_state():
    controller = GenerationController()
    old = controller.start_generation()
    ledger = DeliveryLedger(controller)
    controller.start_generation()
    assert not ledger.commit_visible(SentenceReady(old.generation_id, 0, "迟到。"))
    assert ledger.delivered_text(old.generation_id) == ""
    assert ledger.generated_text(old.generation_id) == ""


def test_x03_report_first_farewell_order_remains_explicit():
    source = (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    report_marker = "# --- Phase 2: Save raw snapshot → Generate report + PDF FIRST ---"
    farewell_marker = "# --- Phase 3: Play farewell TTS AFTER report/PDF is done ---"
    assert source.index(report_marker) < source.index(farewell_marker)


def test_x04_two_controllers_do_not_share_tts_generation_status():
    first = GenerationController()
    first_record = first.start_generation()
    first.cancel_generation(first_record.generation_id, reason="session end")
    second = GenerationController()
    second_record = second.start_generation()
    assert first.current_generation_id is None
    assert second.current_generation_id == second_record.generation_id
    assert second_record.generation_id == 1


def test_x05_deployment_ports_and_voxcpm_selector_remain_unchanged():
    profiles = (ROOT / "deployment" / "profiles.py").read_text(encoding="utf-8")
    assert "8000" in profiles
    assert "8001" in profiles
    assert "a100_80g" in profiles
    selector = (ROOT / "services" / "tts_service.py").read_text(encoding="utf-8")
    assert "tts_service_voxcpm" in selector
