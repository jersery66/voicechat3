"""Synthetic TTS integration over the real generation-scoped delivery path."""

from __future__ import annotations

from threading import Thread

from conversation.contracts import RouterAction
from conversation.delivery import SentenceReady
from tests.e2e.fixtures import ScenarioHarness, proposal


def _wait_for(predicate, timeout=1.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_fixture_sentences_keep_visible_and_tts_order():
    harness = ScenarioHarness(responses=["第一句。第二句。"], chunk_size=2)
    try:
        result, _generation = harness.run_turn("我想说两句", proposal(RouterAction.CHAT, needs_rag=False))
        assert result.turn_decision.action.value == "chat"
        assert [item.text for item in harness.trace.visible_sentences] == ["第一句。", "第二句。"]
        assert _wait_for(lambda: len(harness.trace.tts_calls) == 2)
        assert harness.trace.tts_calls == ["第一句。", "第二句。"]
    finally:
        harness.shutdown()


def test_fixture_cancellation_stops_old_generation_and_rejects_stale_sentence():
    harness = ScenarioHarness(responses=["正在播放。"], chunk_size=2)
    harness.tts.block = True
    try:
        worker = Thread(target=harness.run_turn, args=("旧轮次", proposal(RouterAction.CHAT, needs_rag=False)))
        worker.start()
        assert harness.tts.started.wait(timeout=1.0)
        old_generation = harness.controller.current_generation_id
        new_generation = harness.new_generation()
        assert new_generation > old_generation
        assert not harness.pipeline.delivery_queue.enqueue(SentenceReady(old_generation, 9, "旧尾巴。"))
        worker.join(timeout=2.0)
        assert harness.trace.tts_stop_calls >= 1
        assert all(call != "旧尾巴。" for call in harness.trace.tts_calls)
    finally:
        harness.shutdown()


def test_fixture_provider_failure_does_not_break_visible_delivery():
    harness = ScenarioHarness(responses=["第一句。第二句。"], tts_fail_on="第二句")
    try:
        result, _generation = harness.run_turn("测试播放失败", proposal(RouterAction.CHAT, needs_rag=False))
        assert result.spoken_text
        assert [item.text for item in harness.trace.visible_sentences] == ["第一句。", "第二句。"]
    finally:
        harness.shutdown()


def test_fixture_uses_production_queue_capacity_and_cleanup():
    harness = ScenarioHarness()
    try:
        assert harness.pipeline.delivery_queue.max_pending_sentences == 32
        assert harness.pipeline.delivery_queue.worker_count == 1
    finally:
        harness.shutdown()
