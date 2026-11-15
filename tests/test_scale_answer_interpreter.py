"""Pure natural-language answer interpretation boundary."""

from assessment.scale_runtime import ScaleRuntime
from assessment.answer_interpreter import ScaleAnswerInterpreter


def test_clear_frequency_answer_is_accepted_without_mutating_runtime():
    interpreter = ScaleAnswerInterpreter()

    result = interpreter.interpret(
        "睡不着，几乎每天",
        scale_name="PHQ-9",
        item=3,
    )

    assert result.status == "accepted"
    assert result.score == 3
    assert result.scale_name == "PHQ-9"
    assert result.item == 3


def test_contextual_frequency_answer_can_be_completed_with_pause_request():
    result = ScaleAnswerInterpreter().interpret(
        "几乎每天，先让我休息一下",
        scale_name="PHQ-9",
        item=3,
        question_text="最近睡得怎么样，是睡不着还是睡不好？",
    )

    assert result.status == "accepted"
    assert result.score == 3


def test_denial_does_not_score_an_item_the_question_was_not_about():
    """"没有" answering a different item's question must not book the active one.

    The interpreter only refuses when the question clearly belongs to another
    item; a reply that mentions no symptom at all is handled one layer up, by
    the pipeline's waiting-turn guard.
    """
    result = ScaleAnswerInterpreter().interpret(
        "没有",
        scale_name="PHQ-9",
        item=1,
        question_text="最近睡得怎么样，是睡不着还是睡不好？",
    )

    assert result.status != "accepted"
    assert result.score is None


def test_duration_answer_scores_when_the_question_was_about_the_item():
    """A duration answer names no symptom, so only the question can place it."""
    result = ScaleAnswerInterpreter().interpret(
        "一直都会",
        scale_name="PHQ-9",
        item=1,
        question_text="这段时间，你平时会觉得一些原本还能做的事，现在也提不起劲吗？",
    )

    assert result.status == "accepted"
    assert result.score == 3


def test_pcl5_definition_backed_answer_accepts_score_four():
    result = ScaleAnswerInterpreter().interpret(
        "极度严重",
        scale_name="PCL-5",
        item=1,
    )

    assert result.status == "accepted"
    assert result.score == 4


def test_vague_answer_is_ambiguous_and_has_no_score():
    result = ScaleAnswerInterpreter().interpret(
        "有时候吧",
        scale_name="PHQ-9",
        item=1,
    )

    assert result.status == "ambiguous"
    assert result.score is None


def test_refusal_or_interruption_is_pause_without_score():
    result = ScaleAnswerInterpreter().interpret(
        "不想回答，换个话题",
        scale_name="GAD-7",
        item=1,
    )

    assert result.status == "pause"
    assert result.score is None


def test_unmatched_text_does_not_force_a_numeric_score():
    result = ScaleAnswerInterpreter().interpret(
        "今天的天气还可以",
        scale_name="PHQ-9",
        item=1,
    )

    assert result.status == "unmatched"
    assert result.score is None


def test_interpreter_never_mutates_runtime_answers_or_item():
    runtime = ScaleRuntime()
    runtime.start("PHQ-9")
    before = runtime.snapshot()

    result = ScaleAnswerInterpreter().interpret(
        "有时候吧",
        scale_name=before.active_scale,
        item=before.current_item,
    )

    after = runtime.snapshot()
    assert result.status == "ambiguous"
    assert after == before


def test_assessment_package_exports_interpreter_contract():
    from assessment import ScaleAnswerInterpretation, ScaleAnswerInterpreter

    assert ScaleAnswerInterpretation.__name__ == "ScaleAnswerInterpretation"
    assert ScaleAnswerInterpreter.__name__ == "ScaleAnswerInterpreter"
