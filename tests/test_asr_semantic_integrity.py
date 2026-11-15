"""Correction A1: critical transcript semantics are observed, never guessed."""

from __future__ import annotations

from conversation.contracts import RouterAction, RouterProposal, TurnAction, TurnSignals, TurnStateSnapshot
from conversation.input_semantics import InputSemanticFlags, inspect_input_semantics
from conversation.turn_policy import TurnPolicy
from services.pipeline import correct_asr_text


def test_negation_is_never_flipped_by_asr_normalization():
    raw = "晚上中途不醒"
    corrected, corrections = correct_asr_text(raw)
    assert corrected == raw
    assert corrections == []
    flags = inspect_input_semantics(raw)
    assert flags.negation_ambiguous is False
    assert flags.symptom_polarity_ambiguous is False


def test_frequency_ambiguity_is_observed_without_rewriting_text():
    flags = inspect_input_semantics("频率我记不清")
    assert flags.frequency_ambiguous is True
    corrected, _ = correct_asr_text("大概两三天吧")
    assert corrected == "大概两三天吧"


def test_clear_frequency_duration_and_quantity_values_are_not_ambiguous():
    for text in ("已经两周没睡好了", "每天都会这样", "一周两三天", "发生过两次"):
        flags = inspect_input_semantics(text)
        assert flags.any_ambiguous is False, text


def test_duration_ambiguity_is_observed():
    flags = inspect_input_semantics("不知道多久了")
    assert flags.duration_ambiguous is True


def test_malformed_negation_is_ambiguous_but_clear_negation_is_not():
    assert inspect_input_semantics("中途部醒").negation_ambiguous is True
    assert inspect_input_semantics("中途不醒").negation_ambiguous is False


def test_quantity_ambiguity_is_observed():
    flags = inspect_input_semantics("几次记不清")
    assert flags.quantity_ambiguous is True


def test_safe_normalization_only_normalizes_layout():
    corrected, corrections = correct_asr_text("  最近   有点累  ")
    assert corrected == "最近 有点累"
    assert corrections == ["whitespace_normalized"]


def test_flags_are_observations_and_do_not_mutate_scale_runtime():
    flags = InputSemanticFlags(negation_ambiguous=True, semantic_target="night_waking")
    assert flags.negation_ambiguous is True
    snapshot = TurnStateSnapshot(round_count=8)
    decision = TurnPolicy().decide(
        user_text="晚上中途不醒",
        proposal=RouterProposal(action=RouterAction.CHAT),
        snapshot=snapshot,
        signals=TurnSignals(),
    )
    assert decision.action is TurnAction.CHAT


def test_semantic_observation_does_not_directly_start_a_scale():
    flags = inspect_input_semantics("中途不睡不着")
    assert flags.any_ambiguous is True
    assert flags.semantic_target == "night_waking"


def test_turn_policy_owns_clarify_input_decision():
    decision = TurnPolicy().decide(
        user_text="晚上中途不醒",
        proposal=RouterProposal(action=RouterAction.CHAT),
        snapshot=TurnStateSnapshot(round_count=8),
        signals=TurnSignals(
            semantic_ambiguity=True,
            semantic_target="night_waking",
            semantic_reason="negation_or_sleep_polarity",
        ),
    )
    assert decision.action is TurnAction.CLARIFY_INPUT
    assert decision.needs_rag is False
