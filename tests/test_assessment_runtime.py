"""Assessment runtime owns canonical answer bookkeeping outside the pipeline."""

from assessment.scale_runtime import ScaleRuntime


def test_scale_runtime_advances_to_the_first_unanswered_item():
    runtime = ScaleRuntime()
    runtime.start("PHQ-9", item=1)
    runtime.record_answer(1, 2)
    runtime.record_answer(3, 1)

    assert runtime.next_item(total_items=4) == 2


def test_scale_runtime_rejects_answers_for_non_current_items():
    runtime = ScaleRuntime()
    runtime.start("GAD-7", item=2)

    assert runtime.record_answer(1, 2) is False
