"""A stalled scale item must not deadlock the whole administration.

The incident: PHQ-9 stopped at Q2 for twelve turns.  The writer phrases items
in its own words and sometimes asks them vaguely ("这种不好受的状态"); the
interpreter cannot tie the participant's frequency answer to the open item, so
nothing is booked, the item never advances, and the session ends with three
answers out of nine -- no completion, no score, no relaxation invitation.
"""

from __future__ import annotations

from types import SimpleNamespace

from services.pipeline import ConversationPipeline


def _pipeline_with_open_item() -> tuple[ConversationPipeline, object]:
    pipeline = ConversationPipeline.__new__(ConversationPipeline)
    from assessment import ScaleRuntime

    pipeline.scale_runtime = ScaleRuntime()
    pipeline.scale_runtime.start("PHQ-9")
    pipeline.scale_runtime.present_current_item()
    pipeline._scale_wait_turns = {}
    pipeline._clarify_attempts = {}
    pipeline._scale_unscored_turns = {}
    pipeline._clarification_pending = False
    pipeline._proactive_relaxation_candidate = None
    pipeline._scale_manager = pipeline.scale_runtime._manager
    return pipeline, SimpleNamespace(scale_tags={}, scale_completed=False)


def test_explicit_frequency_is_booked_after_two_unscored_turns():
    pipeline, result = _pipeline_with_open_item()

    assert pipeline._rescue_unscored_item("PHQ-9", 1, "有几天", result) is False
    assert dict(pipeline.scale_runtime.answers_by_scale["PHQ-9"]) == {}

    assert pipeline._rescue_unscored_item("PHQ-9", 1, "有几天", result) is True
    assert dict(pipeline.scale_runtime.answers_by_scale["PHQ-9"]) == {1: 1}
    assert result.scale_tags == {"PHQ-9": {1: 1}}


def test_bare_denial_is_never_rescued():
    """``没有`` carries no topic; booking it would deflate the score silently."""
    pipeline, result = _pipeline_with_open_item()

    for _ in range(5):
        assert pipeline._rescue_unscored_item("PHQ-9", 1, "没有", result) is False

    assert dict(pipeline.scale_runtime.answers_by_scale["PHQ-9"]) == {}


def test_rescue_needs_a_concrete_frequency_not_just_a_long_sentence():
    pipeline, result = _pipeline_with_open_item()

    answer = "这个我也说不好，可能跟我最近跟同屋处不好有关系，反正就是不太舒服"
    assert pipeline._rescue_unscored_item("PHQ-9", 1, answer, result) is False
    assert pipeline._rescue_unscored_item("PHQ-9", 1, answer, result) is False

    assert dict(pipeline.scale_runtime.answers_by_scale["PHQ-9"]) == {}


def test_rescue_advances_to_the_next_item_and_clears_its_counters():
    pipeline, result = _pipeline_with_open_item()
    pipeline._clarify_attempts[("PHQ-9", 1)] = 2
    pipeline._scale_wait_turns[("PHQ-9", 1)] = 4

    for _ in range(2):
        pipeline._rescue_unscored_item("PHQ-9", 1, "一半以上的时间", result)

    snapshot = pipeline.scale_runtime.snapshot()
    assert dict(snapshot.answers_by_scale["PHQ-9"]) == {1: 2}
    assert snapshot.current_item == 2
    assert pipeline._clarify_attempts == {}
    assert pipeline._scale_unscored_turns == {}
    assert pipeline._scale_wait_turns == {}
