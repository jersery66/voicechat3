"""Bounded-admission regressions for the sentence delivery queue."""

from __future__ import annotations

import queue as queue_module

from conversation.delivery import (
    GenerationController,
    SentenceDeliveryQueue,
    SentenceReady,
)


class _ImmediateTTS:
    def generate_and_play(self, _text):
        return None

    def stop_playing(self):
        return None


def _drain(owner: SentenceDeliveryQueue) -> list[SentenceReady]:
    items: list[SentenceReady] = []
    while True:
        try:
            item = owner._queue.get_nowait()
        except queue_module.Empty:
            break
        assert item is not None
        items.append(item)
        owner._queue.task_done()
    return items


def _owner(*, capacity: int = 3):
    controller = GenerationController()
    record = controller.start_generation()
    owner = SentenceDeliveryQueue(
        controller,
        _ImmediateTTS(),
        max_pending_sentences=capacity,
    )
    return controller, record, owner


def test_queue01_default_capacity_is_finite_and_positive():
    controller = GenerationController()
    owner = SentenceDeliveryQueue(controller, _ImmediateTTS())
    assert owner._queue.maxsize > 0
    assert owner._queue.maxsize == 32


def test_queue02_full_capacity_rejects_without_blocking():
    controller, record, owner = _owner(capacity=2)
    assert owner.enqueue(SentenceReady(record.generation_id, 0, "零。"))
    assert owner.enqueue(SentenceReady(record.generation_id, 1, "一。"))
    assert owner.enqueue(SentenceReady(record.generation_id, 2, "二。")) is False
    assert owner._next_seq[record.generation_id] == 2
    _drain(owner)


def test_queue03_failed_admission_does_not_create_sequence_gap():
    controller, record, owner = _owner(capacity=2)
    assert owner.enqueue(SentenceReady(record.generation_id, 0, "零。"))
    assert owner.enqueue(SentenceReady(record.generation_id, 1, "一。"))
    assert owner.enqueue(SentenceReady(record.generation_id, 2, "二。")) is False
    item = owner._queue.get_nowait()
    owner._queue.task_done()
    assert item is not None and item.seq == 0
    assert owner.enqueue(SentenceReady(record.generation_id, 2, "二。"))
    assert [item.seq for item in _drain(owner)] == [1, 2]


def test_queue04_full_queue_compacts_stale_items_before_admission():
    controller, old, owner = _owner(capacity=2)
    current = controller.start_generation()
    stale = SentenceReady(old.generation_id, 99, "旧。")
    owner._queue.put_nowait(stale)
    assert owner.enqueue(SentenceReady(current.generation_id, 0, "新零。"))
    assert owner.enqueue(SentenceReady(current.generation_id, 1, "新一。"))
    retained = _drain(owner)
    assert [(item.generation_id, item.seq) for item in retained] == [
        (current.generation_id, 0),
        (current.generation_id, 1),
    ]


def test_queue05_stale_compaction_preserves_current_fifo_order():
    controller, old, owner = _owner(capacity=4)
    current = controller.start_generation()
    owner.enqueue(SentenceReady(current.generation_id, 0, "零。"))
    owner._queue.put_nowait(SentenceReady(old.generation_id, 4, "旧一。"))
    owner.enqueue(SentenceReady(current.generation_id, 1, "一。"))
    owner._queue.put_nowait(SentenceReady(old.generation_id, 5, "旧二。"))
    assert owner.enqueue(SentenceReady(current.generation_id, 2, "二。"))
    retained = _drain(owner)
    assert [item.seq for item in retained] == [0, 1, 2]
    assert all(item.generation_id == current.generation_id for item in retained)


def test_queue06_current_items_are_not_evicted_for_a_new_current_item():
    controller, record, owner = _owner(capacity=2)
    assert owner.enqueue(SentenceReady(record.generation_id, 0, "零。"))
    assert owner.enqueue(SentenceReady(record.generation_id, 1, "一。"))
    assert owner.enqueue(SentenceReady(record.generation_id, 2, "二。")) is False
    retained = _drain(owner)
    assert [item.seq for item in retained] == [0, 1]
    assert owner._next_seq[record.generation_id] == 2


def test_queue07_cancel_generation_releases_pending_capacity_and_bookkeeping():
    controller, record, owner = _owner(capacity=2)
    assert owner.enqueue(SentenceReady(record.generation_id, 0, "零。"))
    assert owner.enqueue(SentenceReady(record.generation_id, 1, "一。"))
    assert controller.cancel_generation(record.generation_id, reason="interrupt")
    assert owner._queue.qsize() == 0
    assert owner._queue.unfinished_tasks == 0
    assert record.generation_id not in owner._next_seq


def test_queue08_stale_compaction_keeps_unfinished_task_accounting_balanced():
    controller, old, owner = _owner(capacity=2)
    current = controller.start_generation()
    owner.enqueue(SentenceReady(current.generation_id, 0, "零。"))
    owner._queue.put_nowait(SentenceReady(old.generation_id, 4, "旧。"))
    assert owner.enqueue(SentenceReady(current.generation_id, 1, "一。"))
    assert owner._queue.unfinished_tasks == 2
    assert [item.seq for item in _drain(owner)] == [0, 1]
    assert owner._queue.unfinished_tasks == 0
    owner._queue.join()


def test_queue09_shutdown_with_full_queue_is_non_blocking():
    controller, record, owner = _owner(capacity=2)
    owner.start()
    assert owner.enqueue(SentenceReady(record.generation_id, 0, "零。"))
    assert owner.enqueue(SentenceReady(record.generation_id, 1, "一。"))
    owner.shutdown(timeout=1.0)
    assert owner._stopping is True


def test_queue10_stopping_rejects_even_when_capacity_is_available():
    controller, record, owner = _owner(capacity=2)
    owner.shutdown(timeout=1.0)
    assert owner.enqueue(SentenceReady(record.generation_id, 0, "不应入队。")) is False


def test_queue11_large_stale_pressure_never_exceeds_capacity():
    controller, old, owner = _owner(capacity=3)
    current = controller.start_generation()
    for seq in range(100):
        try:
            owner._queue.put_nowait(SentenceReady(old.generation_id, seq, "旧。"))
        except queue_module.Full:
            pass
        assert owner._queue.qsize() <= owner._queue.maxsize
        owner.enqueue(SentenceReady(current.generation_id, seq, "新。"))
        assert owner._queue.qsize() <= owner._queue.maxsize
    _drain(owner)


def test_queue12_worker_lifecycle_uses_one_bounded_queue():
    controller, record, owner = _owner(capacity=2)
    owner.start()
    assert owner.enqueue(SentenceReady(record.generation_id, 0, "零。"))
    owner.shutdown(timeout=2.0)
    assert owner.worker_count == 1
    assert owner._queue.maxsize == 2


def test_queue13_rejects_invalid_capacity():
    controller = GenerationController()
    try:
        SentenceDeliveryQueue(controller, _ImmediateTTS(), max_pending_sentences=0)
    except ValueError:
        pass
    else:
        raise AssertionError("zero queue capacity must be rejected")
