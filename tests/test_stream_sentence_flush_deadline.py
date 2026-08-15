"""Deadline watchdog tests for stalled generation-scoped LLM streams."""

from __future__ import annotations

import threading
import time

from adapters.tts_results import PlaybackResult, PlaybackStatus
from conversation.delivery import (
    DEFAULT_MAX_GENERATION_RECORDS,
    DEFAULT_MAX_PENDING_SENTENCES,
    SentenceReady,
    SentenceSegmenter,
)
from services.pipeline import ConversationPipeline
from tests.integration.fakes import FakeAgent, FakeData, FakeRAG, FakeReport


class _RecordingTTS:
    def __init__(self):
        self.calls: list[str] = []

    def generate_and_play(self, text: str):
        self.calls.append(text)
        return PlaybackResult(PlaybackStatus.COMPLETED)

    def stop_playing(self):
        return None


class _StalledProvider:
    def __init__(self, first: str, second: str = "", *, wait_after_second: bool = False):
        self.first = first
        self.second = second
        self.wait_after_second = wait_after_second
        self.first_requested = threading.Event()
        self.blocked_after_first = threading.Event()
        self.blocked_after_second = threading.Event()
        self.release_first = threading.Event()
        self.release_second = threading.Event()

    def chat(self, _text, system_suffix="", *, commit_history=False):
        self.first_requested.set()
        yield self.first
        self.blocked_after_first.set()
        self.release_first.wait(timeout=5.0)
        if self.second:
            yield self.second
            self.blocked_after_second.set()
            if self.wait_after_second:
                self.release_second.wait(timeout=5.0)


class _PunctuatedProvider:
    def __init__(self, text: str):
        self.text = text

    def chat(self, _text, system_suffix="", *, commit_history=False):
        yield self.text


class _CaptureQueue:
    def __init__(self):
        self.events: list[SentenceReady] = []

    def start(self):
        return None

    def enqueue(self, event: SentenceReady):
        self.events.append(event)
        return True

    def shutdown(self):
        return None


def _wait_for(predicate, timeout: float = 1.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _pipeline(provider, *, max_wait_ms: int = 60):
    tts = _RecordingTTS()
    pipeline = ConversationPipeline(
        stt_service=None,
        llm_service=provider,
        tts_service=tts,
        rag_service=FakeRAG(),
        agent_service=FakeAgent(),
        report_service=FakeReport(),
        data_manager=FakeData(),
        session_emotions=[],
    )
    pipeline._sentence_segmenter_factory = lambda: SentenceSegmenter(
        max_wait_ms=max_wait_ms,
        min_stable_chars=4,
    )
    return pipeline, tts


def _start_stream(pipeline, provider, events):
    record = pipeline.delivery_controller.start_generation()
    thread = threading.Thread(
        target=lambda: pipeline._stream_llm(
            "你好",
            "",
            lambda kind, content: events.append((kind, content)),
            generation_id=record.generation_id,
        ),
        daemon=True,
    )
    thread.start()
    return record, thread


def _finish(pipeline, provider, thread):
    provider.release_first.set()
    provider.release_second.set()
    thread.join(timeout=4.0)
    assert not thread.is_alive()
    pipeline.shutdown()


def test_flush01_stalled_provider_emits_partial_sentence_after_deadline():
    provider = _StalledProvider("听起来你最近一直有点累")
    pipeline, _tts = _pipeline(provider, max_wait_ms=40)
    events: list[tuple[str, object]] = []
    _record, thread = _start_stream(pipeline, provider, events)
    assert provider.blocked_after_first.wait(timeout=2.0)
    try:
        assert _wait_for(lambda: any(kind == "stream_text" for kind, _ in events))
        sentence = next(content for kind, content in events if kind == "stream_text")
        assert isinstance(sentence, SentenceReady)
        assert sentence.text == "听起来你最近一直有点累"
    finally:
        _finish(pipeline, provider, thread)


def test_flush02_deadline_emits_while_provider_remains_blocked():
    provider = _StalledProvider("这几天心里一直有点闷")
    pipeline, _tts = _pipeline(provider, max_wait_ms=40)
    events: list[tuple[str, object]] = []
    _record, thread = _start_stream(pipeline, provider, events)
    assert provider.blocked_after_first.wait(timeout=2.0)
    try:
        assert _wait_for(lambda: any(kind == "stream_text" for kind, _ in events))
        assert not provider.release_first.is_set()
        assert thread.is_alive()
    finally:
        _finish(pipeline, provider, thread)


def test_flush03_short_partial_does_not_bypass_minimum_stable_chars():
    provider = _StalledProvider("嗯")
    pipeline, _tts = _pipeline(provider, max_wait_ms=30)
    events: list[tuple[str, object]] = []
    _record, thread = _start_stream(pipeline, provider, events)
    assert provider.blocked_after_first.wait(timeout=2.0)
    try:
        time.sleep(0.12)
        assert not any(kind == "stream_text" for kind, _ in events)
    finally:
        _finish(pipeline, provider, thread)


def test_flush04_new_chunk_resets_effective_deadline():
    provider = _StalledProvider("最近心里", "有点累", wait_after_second=True)
    pipeline, _tts = _pipeline(provider, max_wait_ms=100)
    events: list[tuple[str, object]] = []
    _record, thread = _start_stream(pipeline, provider, events)
    assert provider.blocked_after_first.wait(timeout=2.0)
    provider.release_first.set()
    assert provider.blocked_after_second.wait(timeout=2.0)
    try:
        time.sleep(0.04)
        assert not any(kind == "stream_text" for kind, _ in events)
        assert _wait_for(lambda: any(kind == "stream_text" for kind, _ in events))
    finally:
        _finish(pipeline, provider, thread)


def test_flush05_punctuation_boundary_is_not_emitted_again():
    provider = _StalledProvider("第一句。")
    pipeline, _tts = _pipeline(provider, max_wait_ms=40)
    events: list[tuple[str, object]] = []
    _record, thread = _start_stream(pipeline, provider, events)
    assert provider.blocked_after_first.wait(timeout=2.0)
    try:
        assert _wait_for(lambda: len(events) >= 1)
        time.sleep(0.1)
        sentences = [content for kind, content in events if kind == "stream_text"]
        assert [sentence.text for sentence in sentences] == ["第一句。"]
    finally:
        _finish(pipeline, provider, thread)


def test_flush06_resume_after_deadline_reuses_segmenter_and_sequence():
    provider = _StalledProvider(
        "听起来你最近一直有点累",
        "，是不是这几天睡得也不好？",
    )
    pipeline, _tts = _pipeline(provider, max_wait_ms=40)
    events: list[tuple[str, object]] = []
    _record, thread = _start_stream(pipeline, provider, events)
    assert provider.blocked_after_first.wait(timeout=2.0)
    assert _wait_for(lambda: len([e for k, e in events if k == "stream_text"]) == 1)
    provider.release_first.set()
    try:
        assert _wait_for(lambda: not thread.is_alive())
        sentences = [content for kind, content in events if kind == "stream_text"]
        assert [sentence.seq for sentence in sentences] == [0, 1]
        assert sentences[0].text == "听起来你最近一直有点累"
        assert sentences[1].text == "，是不是这几天睡得也不好？"
    finally:
        _finish(pipeline, provider, thread)


def test_flush07_cancelled_generation_has_no_deadline_emission():
    provider = _StalledProvider("这一轮已经足够长的旧句子")
    pipeline, _tts = _pipeline(provider, max_wait_ms=40)
    events: list[tuple[str, object]] = []
    record, thread = _start_stream(pipeline, provider, events)
    assert provider.blocked_after_first.wait(timeout=2.0)
    pipeline.delivery_controller.cancel_generation(record.generation_id, reason="interrupt")
    try:
        time.sleep(0.12)
        assert not any(kind == "stream_text" for kind, _ in events)
    finally:
        _finish(pipeline, provider, thread)


def test_flush08_new_generation_replaces_stalled_registration():
    old_provider = _StalledProvider("旧 generation 的完整前缀")
    pipeline, _tts = _pipeline(old_provider, max_wait_ms=40)
    events: list[tuple[str, object]] = []
    old_record, old_thread = _start_stream(pipeline, old_provider, events)
    assert old_provider.blocked_after_first.wait(timeout=2.0)
    new_provider = _StalledProvider("新 generation 的完整前缀")
    pipeline.llm = new_provider
    new_record, new_thread = _start_stream(pipeline, new_provider, events)
    assert new_provider.blocked_after_first.wait(timeout=2.0)
    old_provider.release_first.set()
    try:
        time.sleep(0.12)
        assert not any(
            kind == "stream_text" and content.generation_id == old_record.generation_id
            for kind, content in events
        )
        assert pipeline._active_flush_registration.generation_id == new_record.generation_id
    finally:
        new_provider.release_first.set()
        old_thread.join(timeout=4.0)
        new_thread.join(timeout=4.0)
        pipeline.shutdown()


def test_flush09_old_finalizer_cannot_clear_new_registration():
    old_provider = _StalledProvider("旧 generation 的完整前缀")
    pipeline, _tts = _pipeline(old_provider, max_wait_ms=200)
    events: list[tuple[str, object]] = []
    _old_record, old_thread = _start_stream(pipeline, old_provider, events)
    assert old_provider.blocked_after_first.wait(timeout=2.0)
    new_provider = _StalledProvider("新 generation 的完整前缀")
    pipeline.llm = new_provider
    new_record, new_thread = _start_stream(pipeline, new_provider, events)
    assert new_provider.blocked_after_first.wait(timeout=2.0)
    old_provider.release_first.set()
    assert _wait_for(
        lambda: not old_thread.is_alive(),
        timeout=2.0,
    )
    try:
        assert pipeline._active_flush_registration.generation_id == new_record.generation_id
    finally:
        new_provider.release_first.set()
        new_thread.join(timeout=4.0)
        pipeline.shutdown()


def test_flush10_normal_provider_completion_flushes_remainder_once():
    provider = _PunctuatedProvider("这是最终没有句号但需要一次收尾")
    pipeline, _tts = _pipeline(provider, max_wait_ms=1000)
    events: list[tuple[str, object]] = []
    _record, thread = _start_stream(pipeline, provider, events)
    try:
        thread.join(timeout=4.0)
        assert not thread.is_alive()
        sentences = [content for kind, content in events if kind == "stream_text"]
        assert [sentence.text for sentence in sentences] == ["这是最终没有句号但需要一次收尾"]
    finally:
        pipeline.shutdown()


def test_flush11_one_watchdog_is_reused_across_generations():
    first_provider = _PunctuatedProvider("第一轮完整收尾")
    pipeline, _tts = _pipeline(first_provider, max_wait_ms=1000)
    first_events: list[tuple[str, object]] = []
    _first_record, first_thread = _start_stream(pipeline, first_provider, first_events)
    first_thread.join(timeout=4.0)
    first_watchdog = pipeline._flush_watchdog_thread
    second_provider = _PunctuatedProvider("第二轮完整收尾")
    pipeline.llm = second_provider
    second_events: list[tuple[str, object]] = []
    _second_record, second_thread = _start_stream(pipeline, second_provider, second_events)
    second_thread.join(timeout=4.0)
    try:
        assert first_watchdog is not None
        assert pipeline._flush_watchdog_thread is first_watchdog
    finally:
        pipeline.shutdown()


def test_flush12_shutdown_terminates_watchdog():
    provider = _StalledProvider("需要等待 watchdog 的完整前缀")
    pipeline, _tts = _pipeline(provider, max_wait_ms=500)
    events: list[tuple[str, object]] = []
    _record, thread = _start_stream(pipeline, provider, events)
    assert provider.blocked_after_first.wait(timeout=2.0)
    watchdog = pipeline._flush_watchdog_thread
    assert watchdog is not None and watchdog.is_alive()
    pipeline.shutdown()
    try:
        assert not watchdog.is_alive()
    finally:
        provider.release_first.set()
        thread.join(timeout=4.0)


def test_flush13_sequential_generations_keep_single_watchdog_identity():
    provider = _PunctuatedProvider("第一轮")
    pipeline, _tts = _pipeline(provider, max_wait_ms=1000)
    events: list[tuple[str, object]] = []
    _record, first_thread = _start_stream(pipeline, provider, events)
    first_thread.join(timeout=4.0)
    watchdog = pipeline._flush_watchdog_thread
    provider2 = _PunctuatedProvider("第二轮")
    pipeline.llm = provider2
    _record2, second_thread = _start_stream(pipeline, provider2, events)
    second_thread.join(timeout=4.0)
    try:
        assert pipeline._flush_watchdog_thread is watchdog
    finally:
        pipeline.shutdown()


def test_flush14_deadline_event_uses_normal_emit_and_queue_path():
    provider = _StalledProvider("这是一段会进入正常 delivery 的文本")
    pipeline, _tts = _pipeline(provider, max_wait_ms=40)
    capture_queue = _CaptureQueue()
    pipeline.delivery_queue = capture_queue
    events: list[tuple[str, object]] = []
    _record, thread = _start_stream(pipeline, provider, events)
    assert provider.blocked_after_first.wait(timeout=2.0)
    try:
        assert _wait_for(lambda: bool(capture_queue.events))
        assert any(kind == "stream_text" for kind, _ in events)
        assert capture_queue.events[0].text == "这是一段会进入正常 delivery 的文本"
    finally:
        _finish(pipeline, provider, thread)


def test_flush15_tts20_bounds_remain_unchanged():
    from conversation.delivery import GenerationController, SentenceDeliveryQueue

    controller = GenerationController()
    queue_owner = SentenceDeliveryQueue(controller, _RecordingTTS())
    assert queue_owner._queue.maxsize == DEFAULT_MAX_PENDING_SENTENCES
    assert controller.max_records == DEFAULT_MAX_GENERATION_RECORDS
