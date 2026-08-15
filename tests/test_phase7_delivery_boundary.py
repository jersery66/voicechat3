"""Phase 7 red/green tests for generation-scoped delivery.

The source-boundary checks document the Phase 6 baseline.  The behavioural
tests intentionally describe the new delivery contract before its production
implementation exists.
"""

from __future__ import annotations

import importlib.util
import subprocess
import threading
import time
from pathlib import Path

import pytest

from adapters.tts_results import PlaybackResult, PlaybackStatus

ROOT = Path(__file__).resolve().parents[1]
PHASE7_BASELINE = "38d16ecf2cf59f1d0c74a90b2245b0ebc6e19425"


def _baseline_source(path: str) -> str:
    return subprocess.check_output(
        ["git", "show", f"{PHASE7_BASELINE}:{path}"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    )


def _delivery_types():
    # Keep the first RED failure an assertion rather than a collection error;
    # this proves the requested module is genuinely absent at the baseline.
    assert importlib.util.find_spec("conversation.delivery") is not None, (
        "Phase 7 delivery module has not been introduced"
    )
    from conversation.delivery import (  # type: ignore[import-not-found]
        DeliveryLedger,
        GenerationController,
        SentenceDeliveryQueue,
        SentenceReady,
        SentenceSegmenter,
    )

    return (
        DeliveryLedger,
        GenerationController,
        SentenceDeliveryQueue,
        SentenceReady,
        SentenceSegmenter,
    )


def test_baseline_generation_is_only_main_window_stale_token():
    source = _baseline_source("ui/main_window.py")
    assert "self._pipeline_generation = 0" in source
    assert "self._pipeline_cancel_generation = -1" in source
    assert "generation_id" not in source.split("def _run_pipeline", 1)[1].split(
        "def _post_pipeline_routing", 1
    )[0]


def test_baseline_pipeline_waits_for_full_response_before_tts():
    source = _baseline_source("services/pipeline.py")
    stream_body = source.split("def _stream_llm", 1)[1]
    assert 'generated_text = "".join(' in stream_body
    assert "self._executor.submit(self._play_tts" in source


def test_baseline_tts_async_direct_thread_has_no_generation_contract():
    source = _baseline_source("ui/main_window.py")
    body = source.split("def _play_tts_async", 1)[1].split(
        "def _play_opening_greeting", 1
    )[0]
    assert "threading.Thread" in body
    assert "generate_and_play(text)" in body
    assert "generation_id" not in body


def test_baseline_provider_history_appends_full_response():
    source = _baseline_source("services/llm_service.py")
    assert '"content": self._history_visible_text(full_response)' in source
    assert "delivered_text" not in source


def test_main_window_delivery_paths_are_generation_scoped_after_cutover():
    source = (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert "GenerationController" in source
    assert "SentenceReady" in source
    assert "self._pipeline_generation = 0" not in source
    assert "self._pipeline_cancel_generation = -1" not in source


def test_live_async_tts_path_has_one_delivery_queue_entry_point():
    source = (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    body = source.split("def _play_tts_async", 1)[1].split(
        "def _play_opening_greeting", 1
    )[0]
    assert "threading.Thread" not in body
    assert "generate_and_play(text)" not in body
    assert "delivery_queue" in body


def test_live_pipeline_has_no_direct_whole_response_tts_submit():
    source = (ROOT / "services" / "pipeline.py").read_text(encoding="utf-8")
    assert "self._executor.submit(self._play_tts" not in source
    assert "SentenceDeliveryQueue" in source


def test_generation_ids_are_monotonic_and_previous_generation_is_cancelled():
    _, GenerationController, _, _, _ = _delivery_types()

    controller = GenerationController()
    first = controller.start_generation()
    second = controller.start_generation()

    assert second.generation_id == first.generation_id + 1
    assert first.cancelled is True
    assert controller.is_current(second.generation_id)
    assert not controller.is_current(first.generation_id)


def test_cancel_generation_is_idempotent_and_invalidates_callbacks():
    _, GenerationController, _, _, _ = _delivery_types()

    controller = GenerationController()
    record = controller.start_generation()

    assert controller.cancel_generation(record.generation_id, reason="interrupt") is True
    assert controller.cancel_generation(record.generation_id, reason="duplicate") is False
    assert record.cancelled is True
    assert not controller.is_current(record.generation_id)


def test_sentence_segmenter_handles_one_sentence_and_two_sentences():
    _, GenerationController, _, _, SentenceSegmenter = _delivery_types()
    controller = GenerationController()
    record = controller.start_generation()
    segmenter = SentenceSegmenter()

    events = segmenter.feed(record.generation_id, "你好。最近怎么样？")

    assert [event.text for event in events] == ["你好。", "最近怎么样？"]
    assert [event.seq for event in events] == [0, 1]


def test_sentence_segmenter_keeps_sentence_split_across_provider_chunks():
    _, GenerationController, _, _, SentenceSegmenter = _delivery_types()
    controller = GenerationController()
    record = controller.start_generation()
    segmenter = SentenceSegmenter()

    first = segmenter.feed(record.generation_id, "听起来最近")
    second = segmenter.feed(record.generation_id, "确实挺累的。")

    assert first == []
    assert [event.text for event in second] == ["听起来最近确实挺累的。"]


def test_sentence_segmenter_flushes_trailing_text_once():
    _, GenerationController, _, _, SentenceSegmenter = _delivery_types()
    controller = GenerationController()
    record = controller.start_generation()
    segmenter = SentenceSegmenter()

    assert segmenter.feed(record.generation_id, "你可以慢慢说") == []
    first_flush = segmenter.flush(record.generation_id)
    second_flush = segmenter.flush(record.generation_id)

    assert [event.text for event in first_flush] == ["你可以慢慢说"]
    assert second_flush == []


def test_sentence_segmenter_uses_ellipsis_as_one_boundary():
    _, GenerationController, _, _, SentenceSegmenter = _delivery_types()
    controller = GenerationController()
    record = controller.start_generation()
    segmenter = SentenceSegmenter()

    events = segmenter.feed(record.generation_id, "嗯……我在听。")

    assert [event.text for event in events] == ["嗯……", "我在听。"]


def test_sentence_segmenter_keeps_repeated_terminal_punctuation_together():
    _, GenerationController, _, _, SentenceSegmenter = _delivery_types()
    controller = GenerationController()
    record = controller.start_generation()
    segmenter = SentenceSegmenter()

    events = segmenter.feed(record.generation_id, "真的太难了！！你还好吗？？")

    assert [event.text for event in events] == ["真的太难了！！", "你还好吗？？"]


def test_sentence_ready_keeps_tts_prosody_separate_from_visible_text():
    _, GenerationController, SentenceDeliveryQueue, SentenceReady, _ = _delivery_types()
    controller = GenerationController()
    record = controller.start_generation()
    tts = _FakeTTS()
    queue = SentenceDeliveryQueue(controller, tts)
    queue.start()

    event = SentenceReady(record.generation_id, 0, "你好。", "你好。[breath]")
    assert queue.enqueue(event)
    assert tts.started.wait(timeout=2)
    tts.release.set()
    assert _wait_for(lambda: tts.calls == ["你好。[breath]"])
    queue.shutdown()


def test_sentence_segmenter_flushes_due_bounded_phrase_at_configured_limit():
    _, GenerationController, _, _, SentenceSegmenter = _delivery_types()
    controller = GenerationController()
    record = controller.start_generation()
    segmenter = SentenceSegmenter(max_chars=8, max_wait_ms=0, min_stable_chars=2)

    events = segmenter.feed(record.generation_id, "一二三四五六七八九")

    assert [event.text for event in events] == ["一二三四五六七八"]
    assert [event.text for event in segmenter.flush(record.generation_id)] == ["九"]


def test_sentence_segmenter_does_not_split_inside_unclosed_quote():
    _, GenerationController, _, _, SentenceSegmenter = _delivery_types()
    controller = GenerationController()
    record = controller.start_generation()
    segmenter = SentenceSegmenter()

    first = segmenter.feed(record.generation_id, "她说“我最近很累。")
    second = segmenter.feed(record.generation_id, "”后来睡好了。")

    assert first == []
    assert [event.text for event in second] == ["她说“我最近很累。”", "后来睡好了。"]


def test_delivery_ledger_commits_only_visible_text_once():
    DeliveryLedger, GenerationController, _, SentenceReady, _ = _delivery_types()
    controller = GenerationController()
    record = controller.start_generation()
    ledger = DeliveryLedger(controller)

    assert ledger.commit_visible(SentenceReady(record.generation_id, 0, "第一句。"))
    assert not ledger.commit_visible(SentenceReady(record.generation_id, 0, "第一句。"))
    assert not ledger.commit_visible(SentenceReady(record.generation_id, 2, "跳过第二句。"))
    assert ledger.commit_visible(SentenceReady(record.generation_id, 1, "第二句。"))
    assert ledger.finalize(record.generation_id) == "第一句。第二句。"
    assert ledger.finalize(record.generation_id) == "第一句。第二句。"


def test_delivery_ledger_rejects_stale_visible_text():
    DeliveryLedger, GenerationController, _, SentenceReady, _ = _delivery_types()
    controller = GenerationController()
    old = controller.start_generation()
    ledger = DeliveryLedger(controller)
    new = controller.start_generation()

    assert not ledger.commit_visible(SentenceReady(old.generation_id, 0, "旧句。"))
    assert ledger.commit_visible(SentenceReady(new.generation_id, 0, "新句。"))
    assert ledger.finalize(old.generation_id) == ""
    assert ledger.finalize(new.generation_id) == "新句。"


class _FakeTTS:
    def __init__(self):
        self.calls: list[str] = []
        self.stopped = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def generate_and_play(self, text: str):
        self.calls.append(text)
        self.started.set()
        self.release.wait(timeout=2)
        return PlaybackResult(PlaybackStatus.COMPLETED)

    def stop_playing(self):
        self.stopped += 1
        self.release.set()


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_tts_queue_preserves_sentence_order_and_uses_one_worker():
    _, GenerationController, SentenceDeliveryQueue, SentenceReady, _ = _delivery_types()
    controller = GenerationController()
    record = controller.start_generation()
    tts = _FakeTTS()
    queue = SentenceDeliveryQueue(controller, tts)
    queue.start()

    assert queue.enqueue(SentenceReady(record.generation_id, 0, "第一句。"))
    assert queue.enqueue(SentenceReady(record.generation_id, 1, "第二句。"))
    assert _wait_for(lambda: len(tts.calls) == 1)
    tts.release.set()
    assert _wait_for(lambda: len(tts.calls) == 2)
    queue.shutdown()

    assert tts.calls == ["第一句。", "第二句。"]
    assert queue.worker_count == 1


def test_tts_queue_discards_cancelled_generation_and_stops_playback():
    _, GenerationController, SentenceDeliveryQueue, SentenceReady, _ = _delivery_types()
    controller = GenerationController()
    record = controller.start_generation()
    tts = _FakeTTS()
    queue = SentenceDeliveryQueue(controller, tts)
    queue.start()

    assert queue.enqueue(SentenceReady(record.generation_id, 0, "正在播放。"))
    assert queue.enqueue(SentenceReady(record.generation_id, 1, "不应播放。"))
    assert tts.started.wait(timeout=2)
    controller.cancel_generation(record.generation_id, reason="new turn")
    queue.cancel_generation(record.generation_id)
    assert _wait_for(lambda: tts.stopped >= 1)
    tts.release.set()
    time.sleep(0.05)
    queue.shutdown()

    assert tts.calls == ["正在播放。"]


def test_tts_queue_rejects_stale_sentence_before_provider_call():
    _, GenerationController, SentenceDeliveryQueue, SentenceReady, _ = _delivery_types()
    controller = GenerationController()
    old = controller.start_generation()
    tts = _FakeTTS()
    queue = SentenceDeliveryQueue(controller, tts)
    queue.start()
    controller.start_generation()

    assert not queue.enqueue(SentenceReady(old.generation_id, 0, "旧句。"))
    time.sleep(0.05)
    queue.shutdown()

    assert tts.calls == []


def test_tts_queue_drops_late_playback_completion_callback():
    _, GenerationController, SentenceDeliveryQueue, SentenceReady, _ = _delivery_types()
    controller = GenerationController()
    record = controller.start_generation()
    tts = _FakeTTS()
    events = []
    queue = SentenceDeliveryQueue(controller, tts, on_event=events.append)
    queue.start()

    assert queue.enqueue(SentenceReady(record.generation_id, 0, "会被打断。"))
    assert tts.started.wait(timeout=2)
    controller.cancel_generation(record.generation_id, reason="new turn")
    tts.release.set()
    time.sleep(0.05)
    queue.shutdown()

    assert not any(getattr(event, "generation_id", None) == record.generation_id
                   and event.__class__.__name__ == "AudioFinished"
                   for event in events)


def test_history_finalization_excludes_unseen_generated_tail():
    DeliveryLedger, GenerationController, _, SentenceReady, _ = _delivery_types()
    controller = GenerationController()
    record = controller.start_generation()
    ledger = DeliveryLedger(controller)
    ledger.record_generated(record.generation_id, "A+B+C")
    assert ledger.commit_visible(SentenceReady(record.generation_id, 0, "A"))
    controller.cancel_generation(record.generation_id, reason="user interrupt")

    assert ledger.finalize(record.generation_id) == "A"
    assert ledger.generated_text(record.generation_id) == "A+B+C"


def test_delivery_ledger_finalizes_history_and_persistence_from_visible_text_only():
    DeliveryLedger, GenerationController, _, SentenceReady, _ = _delivery_types()

    class History:
        def __init__(self):
            self.conversation_history = [{"role": "user", "content": "hello"}]

    class Data:
        def __init__(self):
            self.saved = []

        def save_assistant_message(self, audio, text, sample_rate=48000):
            self.saved.append((audio, text, sample_rate))

    controller = GenerationController()
    record = controller.start_generation()
    ledger = DeliveryLedger(controller)
    history = History()
    data = Data()
    ledger.record_generated(record.generation_id, "A+B+C")
    ledger.commit_visible(SentenceReady(record.generation_id, 0, "A"))

    assert ledger.finalize_history(record.generation_id, history, data) == "A"
    assert history.conversation_history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "A"},
    ]
    assert data.saved == [(None, "A", 48000)]
    assert ledger.finalize_history(record.generation_id, history, data) == "A"
    assert data.saved == [(None, "A", 48000)]


def test_stale_cancelled_generation_cannot_write_after_replacement_starts():
    DeliveryLedger, GenerationController, _, SentenceReady, _ = _delivery_types()

    class History:
        conversation_history = [{"role": "user", "content": "hello"}]

    class Data:
        def __init__(self):
            self.saved = []

        def save_assistant_message(self, audio, text, sample_rate=48000):
            self.saved.append((audio, text, sample_rate))

    controller = GenerationController()
    old = controller.start_generation()
    ledger = DeliveryLedger(controller)
    history = History()
    data = Data()
    ledger.commit_visible(SentenceReady(old.generation_id, 0, "旧句。"))
    controller.start_generation()

    assert ledger.finalize_history(old.generation_id, history, data) == ""
    assert data.saved == []


def test_llm_provider_can_defer_assistant_history_commit_to_delivery_ledger():
    from services.llm_service import LLMService

    class Client:
        def chat(self, **kwargs):
            if kwargs["stream"]:
                return iter(
                    [
                        {"message": {"content": "第一句。"}},
                        {"message": {"content": "第二句。"}},
                    ]
                )
            return {"message": {"content": ""}}

    service = LLMService.__new__(LLMService)
    service.client = Client()
    service.model = "test-model"
    service.system_prompt = "system"
    service.history_context = ""
    service.conversation_history = []
    service._maybe_summarize = lambda: None

    assert list(service.chat("hello", commit_history=False)) == ["第一句。", "第二句。"]
    assert service.conversation_history == [{"role": "user", "content": "hello"}]


def test_pipeline_stream_emits_sentence_before_provider_finishes():
    from conversation.delivery import DeliveryLedger, GenerationController, SentenceReady
    from services.pipeline import ConversationPipeline

    class Provider:
        def __init__(self):
            self.events: list[SentenceReady] = []

        def chat(self, _text, system_suffix=None, *, commit_history=True):
            yield "第一句。"
            assert self.events, "first stable sentence was not emitted incrementally"
            yield "第二句。"

    class CaptureQueue:
        def __init__(self):
            self.events: list[SentenceReady] = []

        def enqueue(self, event):
            self.events.append(event)
            return True

    controller = GenerationController()
    record = controller.start_generation()
    provider = Provider()
    queue = CaptureQueue()
    ledger = DeliveryLedger(controller)
    pipeline = ConversationPipeline.__new__(ConversationPipeline)
    pipeline.llm = provider
    pipeline.delivery_controller = controller
    pipeline.delivery_queue = queue
    pipeline.delivery_ledger = ledger
    pipeline._last_stream_generation_id = None
    provider.events = queue.events

    generated, _analysis, tts_text = pipeline._stream_llm(
        "hello",
        None,
        lambda *_event: None,
        generation_id=record.generation_id,
    )

    assert generated == "第一句。第二句。"
    assert tts_text == "第一句。第二句。"
    assert [event.text for event in queue.events] == ["第一句。", "第二句。"]
