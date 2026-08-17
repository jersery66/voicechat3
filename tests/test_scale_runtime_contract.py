"""ScaleRuntime contracts derived from the canonical production registry."""

from __future__ import annotations

from assessment import ScaleAnswerInterpreter, ScaleRuntime
from services.scales import get_scale_manager


def test_all_production_scales_have_ordered_items_and_legal_scores():
    manager = get_scale_manager()
    for name in manager.get_scale_names():
        definition = manager.get_scale_definition(name)
        assert definition is not None
        assert definition.item_count == len(definition.questions)
        assert definition.item_count > 0
        assert definition.legal_scores
        assert all(manager.validate_answer(name, item=1, score=score) for score in definition.legal_scores)


def test_runtime_progression_and_duplicate_rejection_are_deterministic():
    runtime = ScaleRuntime()
    update = runtime.start("PHQ-9")
    assert update.accepted is True
    assert runtime.current_item == 1
    accepted = runtime.accept_answer(1, 0)
    assert accepted.accepted is True
    assert runtime.current_item == 2
    duplicate = runtime.accept_answer(1, 0)
    assert duplicate.accepted is False
    assert duplicate.reason == "not_waiting"


def test_invalid_score_wrong_item_and_ambiguous_answer_do_not_mutate_score():
    runtime = ScaleRuntime()
    runtime.start("GAD-7")
    before = runtime.snapshot()
    assert runtime.accept_answer(2, 0).accepted is False
    assert runtime.accept_answer(1, 99).accepted is False
    interpreter = ScaleAnswerInterpreter()
    interpretation = interpreter.interpret("差不多", scale_name="GAD-7", item=1)
    assert interpretation.status == "ambiguous"
    assert runtime.snapshot().answers_by_scale == before.answers_by_scale


def test_interpreter_does_not_mutate_runtime_and_reset_clears_state():
    runtime = ScaleRuntime()
    runtime.start("PCL-5")
    interpreter = ScaleAnswerInterpreter()
    result = interpreter.interpret("有一点", scale_name="PCL-5", item=1)
    assert result.score == 1
    assert dict(runtime.answers_by_scale["PCL-5"]) == {}
    runtime.accept_answer(1, 1)
    assert runtime.answers_by_scale["PCL-5"][1] == 1
    runtime.reset()
    assert runtime.snapshot().active_scale is None
    assert runtime.answers_by_scale == {}
