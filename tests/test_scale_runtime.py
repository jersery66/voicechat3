"""Deterministic ownership contract for the Phase 3 ScaleRuntime."""

from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from assessment.scale_runtime import ScaleRuntime


def test_start_owns_active_scale_current_item_and_waiting_state():
    runtime = ScaleRuntime()

    update = runtime.start("PHQ-9")
    snapshot = update.snapshot

    assert update.status == "started"
    assert snapshot.active_scale == "PHQ-9"
    assert snapshot.current_item == 1
    assert snapshot.waiting_for_answer is True
    assert snapshot.paused is False
    assert snapshot.answers_by_scale["PHQ-9"] == {}


def test_start_rejects_unknown_scale_and_active_scale_switch():
    runtime = ScaleRuntime()

    assert runtime.start("unknown").accepted is False
    runtime.start("PHQ-9")
    rejected = runtime.start("GAD-7")

    assert rejected.accepted is False
    assert rejected.snapshot.active_scale == "PHQ-9"


def test_accept_answer_advances_to_first_unanswered_item():
    runtime = ScaleRuntime()
    runtime.start("PHQ-9")

    accepted = runtime.accept_answer(1, 2)

    assert accepted.accepted is True
    assert accepted.completed is False
    assert accepted.snapshot.answers_by_scale["PHQ-9"] == {1: 2}
    assert accepted.snapshot.current_item == 2
    assert accepted.snapshot.waiting_for_answer is False


def test_accept_answer_rejects_wrong_scale_item_and_score_without_mutation():
    runtime = ScaleRuntime()
    runtime.start("PHQ-9")
    before = runtime.snapshot()

    for kwargs in (
        {"scale_name": "GAD-7", "item": 1, "score": 1},
        {"scale_name": "PHQ-9", "item": 2, "score": 1},
        {"scale_name": "PHQ-9", "item": 1, "score": 4},
    ):
        rejected = runtime.accept_answer(**kwargs)
        assert rejected.accepted is False
        assert rejected.snapshot == before


def test_duplicate_answer_is_rejected_and_never_overwritten():
    runtime = ScaleRuntime()
    runtime.start("PHQ-9")
    runtime.accept_answer(scale_name="PHQ-9", item=1, score=2)

    rejected = runtime.accept_answer(scale_name="PHQ-9", item=1, score=3)

    assert rejected.accepted is False
    assert rejected.snapshot.answers_by_scale["PHQ-9"] == {1: 2}


def test_completed_scale_cannot_restart_until_reset():
    runtime = ScaleRuntime()
    runtime.start("PCL-5")
    for item in range(1, 9):
        runtime.present_current_item()
        runtime.accept_answer(scale_name="PCL-5", item=item, score=4)

    assert runtime.snapshot().completed_scales == ("PCL-5",)
    rejected = runtime.start("PCL-5")
    assert rejected.accepted is False
    assert rejected.snapshot.active_scale is None

    runtime.reset()
    assert runtime.start("PCL-5").accepted is True


def test_incomplete_scale_resumes_first_actual_unanswered_item():
    runtime = ScaleRuntime()
    runtime.start("GAD-7")
    runtime.accept_answer(scale_name="GAD-7", item=1, score=1)
    runtime.pause()
    resumed = runtime.resume()

    assert resumed.snapshot.current_item == 2
    assert resumed.snapshot.waiting_for_answer is True


def test_clarification_keeps_item_waiting_and_does_not_score():
    runtime = ScaleRuntime()
    runtime.start("PHQ-9")

    update = runtime.request_clarification()

    assert update.status == "clarification_required"
    assert update.accepted is False
    assert update.snapshot.current_item == 1
    assert update.snapshot.waiting_for_answer is True
    assert update.snapshot.answers_by_scale["PHQ-9"] == {}


def test_pause_preserves_actual_unanswered_item_and_resume_recalculates_it():
    runtime = ScaleRuntime()
    runtime.start("PHQ-9")
    runtime.accept_answer(scale_name="PHQ-9", item=1, score=1)
    runtime.request_clarification()

    paused = runtime.pause()
    assert paused.status == "paused"
    assert paused.snapshot.paused is True
    assert paused.snapshot.resume_item == 2
    assert paused.snapshot.waiting_for_answer is False

    resumed = runtime.resume()
    assert resumed.status == "resumed"
    assert resumed.snapshot.paused is False
    assert resumed.snapshot.current_item == 2
    assert resumed.snapshot.resume_item is None
    assert resumed.snapshot.waiting_for_answer is True


def test_completion_clears_active_and_waiting_state():
    runtime = ScaleRuntime()
    runtime.start("GAD-7")
    for item in range(1, 8):
        runtime.present_current_item()
        update = runtime.accept_answer(scale_name="GAD-7", item=item, score=0)

    assert update.accepted is True
    assert update.completed is True
    assert update.snapshot.active_scale is None
    assert update.snapshot.current_item is None
    assert update.snapshot.waiting_for_answer is False
    assert update.snapshot.completed_scales == ("GAD-7",)


def test_snapshot_is_frozen_and_defensive_for_nested_answers():
    runtime = ScaleRuntime()
    runtime.start("PHQ-9")
    runtime.accept_answer(scale_name="PHQ-9", item=1, score=1)
    snapshot = runtime.snapshot()

    with pytest.raises(FrozenInstanceError):
        snapshot.current_item = 3
    with pytest.raises(TypeError):
        snapshot.answers_by_scale["PHQ-9"][1] = 3
    with pytest.raises(TypeError):
        snapshot.answers_by_scale["GAD-7"] = {}


def test_runtime_results_and_incomplete_views_are_derived_read_models():
    runtime = ScaleRuntime()
    runtime.start("PHQ-9")
    runtime.accept_answer(scale_name="PHQ-9", item=1, score=2)

    incomplete = runtime.get_incomplete_scales()
    results = runtime.get_results()

    assert incomplete[0].scale_name == "PHQ-9"
    assert incomplete[0].remaining_nums[0] == 2
    assert results["PHQ-9"]["answered"] == 1
    assert results["PHQ-9"]["total_score"] == 2
    assert isinstance(results, MappingProxyType)


def test_reset_clears_scale_administration_without_model_or_network_calls():
    runtime = ScaleRuntime()
    runtime.start("PHQ-9")
    runtime.accept_answer(scale_name="PHQ-9", item=1, score=1)

    snapshot = runtime.reset()

    assert snapshot.active_scale is None
    assert snapshot.answers_by_scale == {}
    assert snapshot.completed_scales == ()


def test_assessment_package_exports_runtime_read_models():
    from assessment import RuntimeUpdate, ScaleRuntimeSnapshot

    assert RuntimeUpdate.__name__ == "RuntimeUpdate"
    assert ScaleRuntimeSnapshot.__name__ == "ScaleRuntimeSnapshot"


def test_runtime_read_properties_are_snapshot_views():
    runtime = ScaleRuntime()
    runtime.start("PHQ-9")

    assert runtime.active_scale == "PHQ-9"
    assert runtime.current_item == 1
    assert runtime.waiting_for_answer is True
    assert runtime.answers_by_scale["PHQ-9"] == {}
    assert runtime.administered_scales == ("PHQ-9",)
