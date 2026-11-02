"""Admission-control tests for model-produced decisions."""

from __future__ import annotations

import math

import pytest

from decision.contracts import DecisionRequest, RawDecision
from decision.validator import (
    REASON_ACTIVE_SCALE_CONFLICT,
    REASON_BELOW_THRESHOLD,
    REASON_END_SESSION_UNSAFE,
    REASON_GAME_ALREADY_ACTIVE,
    REASON_ILLEGAL_ACTION,
    REASON_INCONSISTENT_ARGMAX,
    REASON_MISSING_ACTION,
    REASON_MISSING_PROBABILITIES,
    REASON_MISSING_SCALE,
    REASON_NEGATIVE_PROBABILITY,
    REASON_NON_FINITE_PROBABILITY,
    REASON_PROBABILITY_OUT_OF_RANGE,
    REASON_PROBABILITY_SUM_INVALID,
    REASON_PROVIDER_ERROR,
    REASON_SESSION_ENDING_CONFLICT,
    REASON_UNKNOWN_SCALE,
    DecisionValidator,
)


def _request(**overrides):
    payload = {"user_text": "我最近总是睡不好，白天也没精神", "current_round": 5}
    payload.update(overrides)
    return DecisionRequest(**payload)


def _decision(**overrides):
    payload = {
        "provider": "test",
        "action": "chat",
        "emotion": "sad",
        "confidence": 0.9,
        "probabilities": {"chat": 0.9, "start_scale": 0.1},
    }
    payload.update(overrides)
    return RawDecision(**payload)


@pytest.fixture()
def validator():
    return DecisionValidator(abstain_threshold=0.55)


def test_accepts_a_well_formed_high_confidence_decision(validator):
    verdict = validator.validate(_decision(), _request())
    assert verdict.accepted is True
    assert verdict.reason is None
    assert verdict.abstained is False


def test_provider_error_is_rejected(validator):
    verdict = validator.validate(_decision(error="gateway_timeout"), _request())
    assert verdict.rejected
    assert verdict.reason == REASON_PROVIDER_ERROR


def test_missing_action_is_rejected(validator):
    verdict = validator.validate(_decision(action=None), _request())
    assert verdict.reason == REASON_MISSING_ACTION


def test_illegal_action_is_rejected(validator):
    verdict = validator.validate(_decision(action="prescribe_ssri"), _request())
    assert verdict.reason == REASON_ILLEGAL_ACTION


def test_missing_probabilities_are_rejected_in_jev_mode(validator):
    verdict = validator.validate(_decision(probabilities={}), _request())
    assert verdict.reason == REASON_MISSING_PROBABILITIES


def test_legacy_validator_can_skip_probability_requirements():
    legacy_validator = DecisionValidator(require_probabilities=False)
    verdict = legacy_validator.validate(_decision(probabilities={}), _request())
    assert verdict.accepted is True


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_probabilities_are_rejected(validator, bad):
    decision = _decision(probabilities={"chat": 0.5, "start_scale": 0.5})
    decision = decision.model_copy(update={"probabilities": {"chat": bad, "start_scale": 0.5}})
    verdict = validator.validate(decision, _request())
    assert verdict.reason == REASON_NON_FINITE_PROBABILITY


def test_negative_probability_is_rejected(validator):
    decision = _decision(probabilities={"chat": -0.2, "start_scale": 1.2})
    verdict = validator.validate(decision, _request())
    # -0.2 is outside the negative tolerance, and 1.2 is outside [0, 1].
    assert verdict.reason in {REASON_NEGATIVE_PROBABILITY, REASON_PROBABILITY_OUT_OF_RANGE}


def test_probability_sum_is_rejected_beyond_tolerance(validator):
    decision = _decision(probabilities={"chat": 0.5, "start_scale": 0.2})
    verdict = validator.validate(decision, _request())
    assert verdict.reason == REASON_PROBABILITY_SUM_INVALID


def test_probability_sum_within_tolerance_is_normalized(validator):
    # Sum 1.005 sits inside the 1e-2 tolerance, so the validator normalises
    # instead of rejecting.  The leading label must still clear the abstain
    # threshold *after* renormalisation (0.605/1.005 ~= 0.602 > 0.55).
    decision = _decision(probabilities={"chat": 0.605, "start_scale": 0.400})
    verdict = validator.validate(decision, _request())
    assert verdict.accepted is True
    assert math.isclose(sum(verdict.decision.probabilities.values()), 1.0, rel_tol=1e-9)


def test_answer_must_be_the_argmax(validator):
    decision = _decision(
        action="chat", probabilities={"chat": 0.2, "start_scale": 0.8}, confidence=0.2
    )
    verdict = validator.validate(decision, _request())
    assert verdict.reason == REASON_INCONSISTENT_ARGMAX


def test_confidence_comes_from_the_distribution_not_the_scalar(validator):
    decision = _decision(
        action="chat", confidence=0.99, probabilities={"chat": 0.2, "start_scale": 0.8}
    )
    verdict = validator.validate(decision, _request())
    # The self-reported 0.99 must not be trusted: the readout says 0.2.
    assert verdict.reason in {REASON_INCONSISTENT_ARGMAX, REASON_BELOW_THRESHOLD}


def test_low_confidence_abstains(validator):
    decision = _decision(action="chat", probabilities={"chat": 0.4, "start_scale": 0.6})
    verdict = validator.validate(decision, _request())
    # argmax is start_scale, so this is an argmax inconsistency first.
    assert verdict.reason in {REASON_INCONSISTENT_ARGMAX, REASON_BELOW_THRESHOLD}


def test_low_confidence_alone_abstains(validator):
    decision = _decision(action="chat", probabilities={"chat": 0.40, "end_session": 0.35, "start_scale": 0.25})
    verdict = validator.validate(decision, _request())
    assert verdict.rejected
    assert verdict.abstained is True
    assert verdict.reason == REASON_BELOW_THRESHOLD


def test_start_scale_requires_a_scale_name(validator):
    decision = _decision(
        action="start_scale", probabilities={"start_scale": 0.9, "chat": 0.1}, confidence=0.9
    )
    verdict = validator.validate(decision, _request())
    assert verdict.reason == REASON_MISSING_SCALE


def test_start_scale_rejects_an_unknown_scale(validator):
    decision = _decision(
        action="start_scale",
        scale_name="BDI-II",
        probabilities={"start_scale": 0.9, "chat": 0.1},
        confidence=0.9,
    )
    verdict = validator.validate(decision, _request())
    assert verdict.reason == REASON_UNKNOWN_SCALE


def test_start_scale_is_rejected_while_another_scale_runs(validator):
    decision = _decision(
        action="start_scale",
        scale_name="GAD-7",
        probabilities={"start_scale": 0.9, "chat": 0.1},
        confidence=0.9,
    )
    verdict = validator.validate(decision, _request(active_scale="PHQ-9"))
    assert verdict.reason == REASON_ACTIVE_SCALE_CONFLICT


def test_any_non_chat_action_is_rejected_while_a_scale_runs(validator):
    decision = _decision(
        action="recommend_relaxation",
        probabilities={"recommend_relaxation": 0.9, "chat": 0.1},
        confidence=0.9,
    )
    verdict = validator.validate(decision, _request(active_scale="PHQ-9"))
    assert verdict.reason == REASON_ACTIVE_SCALE_CONFLICT


def test_non_chat_action_is_rejected_once_the_session_is_ending(validator):
    decision = _decision(
        action="recommend_game",
        probabilities={"recommend_game": 0.9, "chat": 0.1},
        confidence=0.9,
    )
    verdict = validator.validate(decision, _request(session_state="SESSION_ENDING"))
    assert verdict.reason == REASON_SESSION_ENDING_CONFLICT


def test_end_session_is_always_rejected(validator):
    decision = _decision(
        action="end_session",
        probabilities={"end_session": 0.97, "chat": 0.03},
        confidence=0.97,
    )
    verdict = validator.validate(decision, _request())
    assert verdict.rejected
    assert verdict.reason == REASON_END_SESSION_UNSAFE


def test_end_session_rejection_can_be_disabled_for_diagnostics():
    validator = DecisionValidator(reject_end_session=False)
    decision = _decision(
        action="end_session",
        probabilities={"end_session": 0.97, "chat": 0.03},
        confidence=0.97,
    )
    verdict = validator.validate(decision, _request())
    # Even then it is only a proposal; TurnPolicy still requires a deterministic signal.
    assert verdict.accepted is True


def test_game_recommendation_is_rejected_while_a_game_runs(validator):
    decision = _decision(
        action="recommend_game",
        probabilities={"recommend_game": 0.9, "chat": 0.1},
        confidence=0.9,
    )
    verdict = validator.validate(decision, _request(game_active=True))
    assert verdict.reason == REASON_GAME_ALREADY_ACTIVE


def test_threshold_must_be_usable():
    with pytest.raises(ValueError):
        DecisionValidator(abstain_threshold=0.0)
    with pytest.raises(ValueError):
        DecisionValidator(abstain_threshold=1.5)


def test_describe_is_metadata_only(validator):
    described = validator.describe()
    assert described["abstain_threshold"] == 0.55
    assert "user_text" not in described
