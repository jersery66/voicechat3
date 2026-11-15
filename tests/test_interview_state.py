"""What the shared interview state accumulates, and what it refuses to.

The decision prompt asks a longitudinal question, so the evidence block is input
in the same sense as the current utterance is.  Two properties matter and are
tested separately:

1. it describes what was actually said (duration, frequency, impact, denials,
   contradictions), and
2. it is derived from text alone -- an annotation such as ``domains`` must never
   change it, because a label in the prompt invalidates the measurement.
"""

from __future__ import annotations

from conversation.interview_state import (
    SYMPTOM_DOMAINS,
    InterviewState,
    format_history,
    symptom_turns_from_deltas,
)


# ---------------------------------------------------------------------------
# Accumulation
# ---------------------------------------------------------------------------


def test_domains_accumulate_across_turns_instead_of_resetting():
    state = InterviewState()
    state.record_user("晚上总是醒，睡不好。", round_count=1)  # note: "总是" is a frequency cue
    state.record_user("白天上班也累，提不起劲。", round_count=2)
    state.record_user("什么都不想吃。", round_count=3)

    assert "睡眠" in state.domains
    assert "精力" in state.domains
    assert "食欲" in state.domains
    assert state.missing_domains() and "睡眠" not in state.missing_domains()


def test_a_single_turn_summary_is_not_the_same_as_a_session_summary():
    """The defect: the old evaluator described only the current turn."""
    state = InterviewState()
    state.record_user("最近睡不着。", round_count=1)
    first = state.symptom_summary()
    state.record_user("也没胃口，瘦了十斤。", round_count=2)
    later = state.symptom_summary()

    assert "方面的主诉" in first and "睡眠" in first.split("；")[0]
    assert "食欲" not in first.split("；")[0]
    assert "睡眠" in later.split("；")[0] and "食欲" in later.split("；")[0]


def test_duration_and_functional_impact_are_captured_verbatim():
    state = InterviewState()
    state.record_user("这种状态已经一个多月了，上班也干不了活。", round_count=4)
    summary = state.symptom_summary()

    assert state.duration == "一个多月"
    assert "工作" in state.impairments
    assert "持续一个多月" in summary
    assert "影响" in summary


def test_frequency_keeps_the_strongest_statement():
    state = InterviewState()
    state.record_user("偶尔会醒。", round_count=1)
    state.record_user("其实几乎每天都醒。", round_count=2)
    state.record_user("有时候也还好。", round_count=3)
    assert state.frequency == "几乎每天"


def test_denials_are_recorded_with_the_round_they_happened():
    state = InterviewState()
    state.record_user("我没有失眠，睡得挺好。", round_count=2)
    assert "睡眠" in state.denials
    assert state.denials["睡眠"] == 2
    assert "明确否认" in state.symptom_summary()
    assert "睡眠" not in state.domains


def test_contradiction_across_rounds_is_flagged():
    state = InterviewState()
    state.record_user("晚上老是醒，睡不好。", round_count=2)
    state.record_user("其实我睡得挺好，刚才是随口说的。", round_count=5)

    assert state.contradictions
    assert "睡眠" in state.contradictions[0]
    assert "前后不一致" in state.symptom_summary()


def test_denying_and_asserting_in_the_same_turn_is_not_a_contradiction():
    state = InterviewState()
    state.record_user("不是睡不好，是早醒。", round_count=3)
    assert state.contradictions == []
    assert "睡眠" in state.domains


def test_the_bare_character_bie_is_not_a_negation():
    """「特别」must not read as a denial of what follows."""
    state = InterviewState()
    state.record_user("我现在特别烦，心里堵得慌。", round_count=1)
    assert state.denials == {}
    assert "情绪" in state.domains


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def test_history_is_bounded_per_line_and_per_turn():
    state = InterviewState(max_turns=2, per_turn_chars=10)
    for index in range(6):
        state.record_user(f"第{index}轮说了一句很长很长的话，长到需要被截断。", round_count=index + 1)
        state.record_assistant("嗯。")

    text = state.history_text()
    lines = text.splitlines()
    assert len(lines) <= 4
    assert all(len(line) <= len("来访者: ") + 10 for line in lines)


def test_history_labels_match_the_production_transcript():
    text = format_history(
        [
            {"role": "user", "content": "我睡不着。"},
            {"role": "assistant", "content": "多久了？"},
        ]
    )
    assert text.splitlines() == ["来访者: 我睡不着。", "小薇: 多久了？"]


def test_empty_messages_do_not_create_blank_lines():
    text = format_history(
        [{"role": "user", "content": "  "}, {"role": "assistant", "content": "嗯。"}]
    )
    assert text == "小薇: 嗯。"


# ---------------------------------------------------------------------------
# Symptom-turn counting parity with production
# ---------------------------------------------------------------------------


def test_symptom_turn_counting_matches_the_production_rule():
    assert symptom_turns_from_deltas({"PHQ-9": 2, "GAD-7": 0}) is True
    assert symptom_turns_from_deltas({"PHQ-9": 0, "GAD-7": 0}) is False
    assert symptom_turns_from_deltas({}) is False


def test_symptom_turns_are_not_counted_while_a_scale_is_running():
    state = InterviewState()
    state.record_user("2分。", round_count=4, assessment_active=True, deltas={"PHQ-9": 2})
    assert state.symptom_turns == 0
    state.record_user("睡不好。", round_count=5, assessment_active=False, deltas={"PHQ-9": 2})
    assert state.symptom_turns == 1


def test_reset_clears_every_accumulated_fact():
    state = InterviewState()
    state.record_user("睡不好，已经两个月了。", round_count=1)
    state.reset()
    assert state.domains == set()
    assert state.duration is None
    assert state.history_text() == ""
    assert state.symptom_summary() == ""


def test_every_domain_name_comes_from_one_table():
    """The table moved out of the pipeline; it must still describe itself fully."""
    assert "睡眠" in SYMPTOM_DOMAINS
    assert len(set(SYMPTOM_DOMAINS)) == len(SYMPTOM_DOMAINS)
