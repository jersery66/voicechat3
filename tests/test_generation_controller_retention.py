"""Bounded transient retention tests for GenerationController."""

from __future__ import annotations

from conversation import delivery as delivery_module
from conversation.delivery import (
    DeliveryLedger,
    GenerationCancelled,
    GenerationController,
    SentenceReady,
)


class _HistoryOwner:
    def __init__(self):
        self.conversation_history: list[dict[str, str]] = []


class _DataManager:
    def __init__(self):
        self.calls: list[tuple[object, str]] = []

    def save_assistant_message(self, subject_id, content, *, sample_rate=48000):
        self.calls.append((subject_id, content))


def test_retention01_default_max_records_is_finite_and_positive():
    controller = GenerationController()
    assert controller.max_records == getattr(
        delivery_module, "DEFAULT_MAX_GENERATION_RECORDS", 0
    )
    assert isinstance(controller.max_records, int)
    assert controller.max_records > 0


def test_retention02_many_generations_never_exceed_configured_bound():
    controller = GenerationController(max_records=3)
    for _ in range(100):
        controller.start_generation()
        assert len(controller._records) <= 3


def test_retention03_oldest_records_are_pruned_deterministically():
    controller = GenerationController(max_records=3)
    records = [controller.start_generation() for _ in range(5)]
    assert list(controller._records) == [3, 4, 5]
    assert [record.generation_id for record in records[-3:]] == [3, 4, 5]


def test_retention04_current_generation_is_never_pruned():
    controller = GenerationController(max_records=2)
    first = controller.start_generation()
    current = controller.start_generation()
    assert controller.current_generation_id == current.generation_id
    assert controller.get_record(current.generation_id) is current
    assert controller.is_current(current.generation_id)
    assert controller.get_record(first.generation_id) is first


def test_retention05_bound_one_keeps_new_current_generation():
    controller = GenerationController(max_records=1)
    first = controller.start_generation()
    second = controller.start_generation()
    assert controller.current_generation_id == second.generation_id
    assert controller.get_record(first.generation_id) is None
    assert controller.get_record(second.generation_id) is second
    assert controller.is_current(second.generation_id)


def test_retention06_generation_ids_remain_monotonic_after_pruning():
    controller = GenerationController(max_records=2)
    for expected in range(1, 51):
        record = controller.start_generation()
        assert record.generation_id == expected
    assert controller.start_generation().generation_id == 51


def test_retention07_pruned_get_record_returns_none():
    controller = GenerationController(max_records=2)
    first = controller.start_generation()
    controller.start_generation()
    controller.start_generation()
    assert controller.get_record(first.generation_id) is None


def test_retention08_pruned_is_current_is_false():
    controller = GenerationController(max_records=1)
    first = controller.start_generation()
    controller.start_generation()
    assert controller.is_current(first.generation_id) is False


def test_retention09_pruned_cancel_is_false_and_cannot_affect_current():
    controller = GenerationController(max_records=1)
    first = controller.start_generation()
    current = controller.start_generation()
    assert controller.cancel_generation(first.generation_id, reason="late") is False
    assert controller.current_generation_id == current.generation_id
    assert controller.is_current(current.generation_id)


def test_retention10_listener_fires_when_superseded_record_is_pruned():
    events: list[GenerationCancelled] = []
    controller = GenerationController(max_records=1, on_cancel=events.append)
    first = controller.start_generation()
    second = controller.start_generation()
    assert second.generation_id == 2
    assert events == [
        GenerationCancelled(first.generation_id, "superseded by new generation")
    ]


def test_retention11_retained_cancelled_record_keeps_event_semantics():
    controller = GenerationController(max_records=3)
    record = controller.start_generation()
    assert controller.cancel_generation(record.generation_id, reason="interrupt")
    retained = controller.get_record(record.generation_id)
    assert retained is record
    assert retained.cancelled is True
    assert retained.cancel_reason == "interrupt"
    assert retained.cancellation_event.is_set()
    assert controller.cancel_generation(record.generation_id, reason="again") is False


def test_retention12_ledger_commit_for_pruned_generation_fails_closed():
    controller = GenerationController(max_records=2)
    first = controller.start_generation()
    ledger = DeliveryLedger(controller)
    assert ledger.commit_visible(SentenceReady(first.generation_id, 0, "旧句。"))
    controller.start_generation()
    current = controller.start_generation()
    assert controller.get_record(first.generation_id) is None
    assert not ledger.commit_visible(SentenceReady(first.generation_id, 1, "迟到。"))
    assert ledger.delivered_text(current.generation_id) == ""


def test_retention13_pruned_finalize_history_does_not_write_or_append():
    controller = GenerationController(max_records=2)
    first = controller.start_generation()
    ledger = DeliveryLedger(controller)
    owner = _HistoryOwner()
    data = _DataManager()
    assert ledger.commit_visible(SentenceReady(first.generation_id, 0, "旧句。"))
    controller.start_generation()
    controller.start_generation()
    assert ledger.finalize_history(first.generation_id, owner, data) == ""
    assert owner.conversation_history == []
    assert data.calls == []


def test_retention14_durable_history_survives_transient_record_pruning():
    controller = GenerationController(max_records=2)
    first = controller.start_generation()
    ledger = DeliveryLedger(controller)
    owner = _HistoryOwner()
    data = _DataManager()
    assert ledger.commit_visible(SentenceReady(first.generation_id, 0, "已保存。"))
    assert ledger.finalize_history(first.generation_id, owner, data) == "已保存。"
    controller.start_generation()
    controller.start_generation()
    assert controller.get_record(first.generation_id) is None
    assert owner.conversation_history == [{"role": "assistant", "content": "已保存。"}]
    assert data.calls == [(None, "已保存。")]


def test_retention15_stress_thousands_of_generations():
    controller = GenerationController(max_records=3)
    for _ in range(1000):
        controller.start_generation()
    assert len(controller._records) <= 3
    assert controller.current_generation_id == 1000


def test_retention16_cancel_listener_is_not_duplicated_or_pruned():
    events: list[GenerationCancelled] = []
    controller = GenerationController(max_records=2, on_cancel=events.append)
    controller.add_cancel_listener(events.append)
    controller.add_cancel_listener(events.append)
    for _ in range(20):
        controller.start_generation()
    assert len(events) == 19
    assert len(controller._cancel_listeners) == 1
