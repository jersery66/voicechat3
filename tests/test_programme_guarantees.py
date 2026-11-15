"""Two programme promises every participant is entitled to.

1. The questionnaire can actually be completed.  Item 9 asks about self-harm,
   and the standard answer is a denial -- which used to be read as a
   disclosure, sending the turn down the risk path and discarding the answer,
   so every instrument ended 8/9 and no score was ever produced.
2. One relaxation exercise is offered per session, whether or not the
   questionnaire finished.  The invitation used to be produced only by a
   completed scale, so a session whose instrument stalled never offered
   anything at all.
"""

from __future__ import annotations

import pytest

from conversation.contracts import RouterAction, TurnAction
from conversation.turn_signals import detect_risk_marker
from config import MIN_ROUNDS_BEFORE_SCALE, MIN_ROUNDS_FOR_RELAXATION
from services.scales import get_scale_manager

from tests.e2e.fixtures import ScenarioHarness, proposal


@pytest.fixture
def harness():
    value = ScenarioHarness(start_round=0)
    try:
        yield value
    finally:
        value.shutdown()


# --- the denial that ends a questionnaire ------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        "没有，从来没有伤害自己的想法。",
        "没有",
        "我从来没有想过自杀",
        "最近没想过伤害自己",
        "我不会自杀的",
    ],
)
def test_a_denial_of_ideation_is_not_a_risk_disclosure(utterance):
    assert detect_risk_marker(utterance) is False


@pytest.mark.parametrize(
    "utterance",
    [
        "我不想活了",
        "我想死",
        "不如就这么算了",
        "死了算了",
        "我没有工作，也不想活了",
        "我控制不住想死",
        # Spoken variants.  A real session said both of these and neither was
        # recognised: the turn was handled as ordinary chat and the writer asked
        # how the feeling felt instead of stopping to check safety.
        "觉得活着没什么意思，有时候想就这么睡过去算了。",
        "有时候真的想结束这一切。",
        "活着是累赘",
        "不想再撑了",
    ],
)
def test_an_asserted_statement_still_reaches_the_risk_path(utterance):
    assert detect_risk_marker(utterance) is True


@pytest.mark.parametrize(
    "utterance",
    [
        "这电影没什么意思，看到一半就关了。",
        "工作没劲，想辞职。",
        "最近什么都不想干，躺着也不想动。",
    ],
)
def test_ordinary_complaints_are_not_read_as_ideation(utterance):
    """The wider marker list must not turn everyday fatigue into a safety event.

    Every added variant keeps a self-referential subject ("活着", "睡过去"),
    so complaining about a film or a job cannot reach the risk path.
    """
    assert detect_risk_marker(utterance) is False


def test_phq9_completes_when_item_nine_is_answered_with_a_denial(harness):
    definition = get_scale_manager().get_scale_definition("PHQ-9")
    assert definition is not None
    harness.report.round_count = MIN_ROUNDS_BEFORE_SCALE

    harness.run_turn(
        "开始这个量表",
        proposal(RouterAction.START_SCALE, scale="PHQ-9", needs_rag=True),
    )
    for _item in range(definition.item_count - 1):
        harness.run_turn("几乎每天都有", proposal(RouterAction.CHAT, needs_rag=False))
    result, _generation = harness.run_turn(
        "没有，从来没有伤害自己的想法。",
        proposal(RouterAction.CHAT, needs_rag=False),
    )

    runtime = harness.pipeline.scale_runtime.snapshot()
    assert "PHQ-9" in runtime.completed_scales
    assert runtime.active_scale is None
    # The denial must not put the rest of the session on the safety path.
    assert harness.pipeline._risk_disclosed is False
    assert result.scale_completed is True


# --- one exercise, whether or not the questionnaire finished -----------------


def test_session_without_a_completed_scale_still_recommends_one_exercise(harness):
    harness.report.round_count = MIN_ROUNDS_FOR_RELAXATION
    harness.run_turn("最近睡得还行。", proposal(RouterAction.CHAT, needs_rag=False))

    assert harness.pipeline._proactive_relaxation_candidate is not None
    assert harness.pipeline._relaxation_recommendation in {"breathing", "muscle", "meditation"}

    result, _generation = harness.run_turn(
        "嗯，接着聊。", proposal(RouterAction.CHAT, needs_rag=False)
    )
    assert result.turn_decision.action is TurnAction.RECOMMEND_RELAXATION
    assert result.turn_decision.reason == "proactive_relaxation_accepted"
    # The type must survive to the UI layer, otherwise the Center opens with
    # nothing highlighted.
    assert result.relaxation_rec == harness.pipeline._relaxation_recommendation


def test_the_exercise_is_offered_only_once_per_session(harness):
    harness.report.round_count = MIN_ROUNDS_FOR_RELAXATION
    harness.run_turn("最近睡得还行。", proposal(RouterAction.CHAT, needs_rag=False))
    harness.run_turn("嗯，接着聊。", proposal(RouterAction.CHAT, needs_rag=False))

    result, _generation = harness.run_turn(
        "最近工作挺忙的。", proposal(RouterAction.CHAT, needs_rag=False)
    )
    assert result.turn_decision.action is not TurnAction.RECOMMEND_RELAXATION


def test_the_sleep_answer_chooses_the_body_scan_not_breathing(harness):
    harness.pipeline._focus_counts = {"sleep": 2, "energy": 1}
    assert harness.pipeline._relaxation_type_from_focus() == "meditation"


def test_anxious_session_chooses_the_muscle_sequence(harness):
    harness.pipeline.symptom_scores = {"GAD-7": 6}
    assert harness.pipeline._relaxation_type_from_focus() == "muscle"


#: One plausible answer per PHQ-9 item.  The sleep and energy answers are
#: severe on purpose: they are what should steer the recommendation to the
#: body scan rather than the default breathing exercise.
PHQ_ANSWERS = {
    1: "几乎每天都是，什么都不想干。",
    2: "天天都觉得闷，高兴不起来。",
    3: "几乎每天都要一两个小时才睡着，半夜还老醒。",
    4: "几乎每天都累，浑身没力气。",
    5: "经常吃不下，没胃口。",
    6: "经常觉得自己挺失败的。",
    7: "几乎每天注意力都集中不了。",
    8: "经常坐立不安，心里烦躁。",
    9: "没有，从来没有伤害自己的想法。",
}


def test_a_completed_sleep_scale_names_the_exercise_it_recommends(harness):
    definition = get_scale_manager().get_scale_definition("PHQ-9")
    assert definition is not None
    harness.report.round_count = MIN_ROUNDS_BEFORE_SCALE
    harness.run_turn(
        "开始这个量表",
        proposal(RouterAction.START_SCALE, scale="PHQ-9", needs_rag=True),
    )

    for _item in range(definition.item_count):
        runtime = harness.pipeline.scale_runtime.snapshot()
        if runtime.active_scale is None:
            break
        answer = PHQ_ANSWERS[runtime.current_item]
        harness.run_turn(answer, proposal(RouterAction.CHAT, needs_rag=False))

    runtime = harness.pipeline.scale_runtime.snapshot()
    assert "PHQ-9" in runtime.completed_scales
    assert harness.pipeline._relaxation_recommendation == "meditation"

    result, _generation = harness.run_turn(
        "行，那你接着说吧。", proposal(RouterAction.CHAT, needs_rag=False)
    )
    assert result.turn_decision.action is TurnAction.RECOMMEND_RELAXATION
    # The concrete exercise must survive to the UI, otherwise the Center opens
    # with nothing highlighted and the writer cannot name the exercise.
    assert result.relaxation_rec == "meditation"
