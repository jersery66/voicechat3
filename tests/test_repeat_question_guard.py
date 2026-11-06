"""Repeat-question guards: colloquial answers score, asked questions are remembered."""

from pathlib import Path

from assessment.answer_interpreter import ScaleAnswerInterpreter
from services.pipeline import ConversationPipeline

ROOT = Path(__file__).resolve().parents[1]


def _pipeline():
    pipeline = ConversationPipeline.__new__(ConversationPipeline)
    pipeline._recent_questions = []
    return pipeline


def test_colloquial_frequency_answers_score():
    interpreter = ScaleAnswerInterpreter()
    # "一半吧" is how participants answer; it must not leave the item pending.
    assert interpreter.interpret("一半吧", scale_name="PHQ-9", item=2).score == 2
    assert interpreter.interpret("经常", scale_name="PHQ-9", item=2).score == 2
    assert interpreter.interpret("几乎每天", scale_name="PHQ-9", item=2).score == 3
    assert interpreter.interpret("很少", scale_name="PHQ-9", item=2).score == 1
    assert interpreter.interpret("几乎没有", scale_name="PHQ-9", item=2).score == 0


def test_questions_are_remembered_without_duplicates():
    pipeline = _pipeline()
    pipeline._record_questions("这种没劲的感觉，是从什么时候开始的？")
    pipeline._record_questions("除了心里低落和睡不好，最近有没有觉得特别烦躁，或者看什么都不顺眼的时候？")
    pipeline._record_questions("那除了心里难受和睡不好，吃饭还正常吗？")
    pipeline._record_questions("除了心里低落和睡不好，最近有没有觉得特别烦躁，或者看什么都不顺眼的时候？")

    assert len(pipeline._recent_questions) == 3
    assert len(set(pipeline._recent_questions)) == 3
    assert pipeline._recent_questions[-1].startswith("那除了心里难受")


def test_no_repeat_context_names_the_questions_and_forbids_rephrasing():
    pipeline = _pipeline()
    pipeline._record_questions("最近有没有觉得特别烦躁，或者看什么都不顺眼的时候？")
    context = pipeline._build_no_repeat_context()
    assert "不要重复提问" in context
    assert "换个说法再问一遍也算重复" in context
    assert "特别烦躁" in context


def test_empty_history_has_no_repeat_block():
    assert _pipeline()._build_no_repeat_context() == ""


def test_clarification_attempts_are_budgeted():
    source = (ROOT / "services" / "pipeline.py").read_text(encoding="utf-8")
    assert "_clarify_attempts" in source
    assert "attempts < 2" in source
