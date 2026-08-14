"""Assessment runtime owns canonical answer bookkeeping outside the pipeline."""

from assessment.scale_runtime import ScaleRuntime


def test_scale_runtime_advances_to_the_first_unanswered_item():
    runtime = ScaleRuntime()
    runtime.start("PHQ-9")
    runtime.record_answer(1, 2)
    runtime.record_answer(3, 1)

    assert runtime.next_item(total_items=4) == 2


def test_scale_runtime_rejects_answers_for_non_current_items():
    runtime = ScaleRuntime()
    runtime.start("GAD-7")

    assert runtime.accept_answer(item=2, score=2).accepted is False
