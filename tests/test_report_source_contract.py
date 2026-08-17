"""Delivered-text and report/data sink contracts."""

from __future__ import annotations

from conversation.delivery import DeliveryLedger, GenerationController, SentenceReady


class _History:
    def __init__(self):
        self.conversation_history = []


class _Data:
    def __init__(self):
        self.writes = []

    def save_assistant_message(self, _audio, text, sample_rate=48000):
        self.writes.append((text, sample_rate))


def test_report_sink_receives_only_visible_delivered_text():
    controller = GenerationController()
    ledger = DeliveryLedger(controller)
    history = _History()
    data = _Data()
    record = controller.start_generation()
    ledger.record_generated(record.generation_id, "可见句。未交付尾巴。")
    assert ledger.commit_visible(SentenceReady(record.generation_id, 0, "可见句。"))
    assert ledger.finalize_history(record.generation_id, history, data) == "可见句。"
    assert history.conversation_history == [{"role": "assistant", "content": "可见句。"}]
    assert data.writes == [("可见句。", 48000)]


def test_stale_cancelled_generation_cannot_write_report_or_data():
    controller = GenerationController()
    ledger = DeliveryLedger(controller)
    history = _History()
    data = _Data()
    old = controller.start_generation()
    ledger.record_generated(old.generation_id, "旧生成。")
    controller.cancel_generation(old.generation_id, reason="new turn")
    controller.start_generation()
    assert ledger.finalize_history(old.generation_id, history, data) == ""
    assert history.conversation_history == []
    assert data.writes == []


def test_report_sink_failure_does_not_duplicate_visible_history():
    controller = GenerationController()
    ledger = DeliveryLedger(controller)
    history = _History()

    class FailingData:
        def save_assistant_message(self, *_args, **_kwargs):
            raise OSError("synthetic disk failure")

    record = controller.start_generation()
    ledger.commit_visible(SentenceReady(record.generation_id, 0, "已交付。"))
    assert ledger.finalize_history(record.generation_id, history, FailingData()) == "已交付。"
    assert ledger.finalize_history(record.generation_id, history, FailingData()) == "已交付。"
    assert history.conversation_history == [{"role": "assistant", "content": "已交付。"}]
