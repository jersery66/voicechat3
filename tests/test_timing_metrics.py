"""Timing metrics: windows, refusals, and the rates reported to the reader.

A timing metric that grades a single "correct round" would punish a system that
offered one turn late inside an acceptable window.  These tests pin the window
semantics so the reported numbers keep meaning what the report claims.
"""

from __future__ import annotations

from decision.timing_metrics import aggregate, evaluate_trajectory, format_report


def turn(round_number, *, intervention="continue_interview", offers=(), **overrides):
    record = {
        "round": round_number,
        "intervention": intervention,
        "offers": list(offers),
        "policy_action": "chat",
        "executed": "chat",
        "active_scale": None,
        "accepted_offer_kind": None,
        "accepted_offer_target": None,
        "declined_kind": None,
        "listening": False,
        "expect": None,
    }
    record.update(overrides)
    return record


def trajectory(**overrides):
    data = {"id": "T", "title": "t", "split": "dev", "windows": {}, "turns": []}
    data.update(overrides)
    return data


SCALE_OFFER = {"kind": "scale", "target": "PHQ-9"}
RELAX_OFFER = {"kind": "relaxation", "target": None}


def test_offering_inside_the_window_is_a_hit():
    result = evaluate_trajectory(
        trajectory(windows={"scale": [6, 8]}),
        [turn(5), turn(7, intervention="offer_scale", offers=[SCALE_OFFER])],
    )
    assert result["scale_outcome"] == "hit"
    assert result["scale_delay"] == 0
    assert result["scale_first_offer"] == 7


def test_offering_before_the_window_is_early_not_wrong():
    result = evaluate_trajectory(
        trajectory(windows={"scale": [6, 8]}),
        [turn(4, intervention="offer_scale", offers=[SCALE_OFFER])],
    )
    assert result["scale_outcome"] == "early"
    assert result["scale_delay"] == -2


def test_offering_after_the_window_is_late_and_the_delay_is_measured():
    result = evaluate_trajectory(
        trajectory(windows={"scale": [6, 8]}),
        [turn(9, intervention="offer_scale", offers=[SCALE_OFFER])],
    )
    assert result["scale_outcome"] == "late"
    assert result["scale_delay"] == 3


def test_never_offering_inside_a_window_is_missed():
    result = evaluate_trajectory(
        trajectory(windows={"scale": [6, 8]}), [turn(6), turn(7)]
    )
    assert result["scale_outcome"] == "missed"


def test_offering_when_no_window_exists_is_spurious():
    result = evaluate_trajectory(
        trajectory(windows={}),
        [turn(5, intervention="offer_scale", offers=[SCALE_OFFER])],
    )
    assert result["scale_outcome"] == "spurious"


def test_repeating_an_offer_inside_the_cooldown_is_a_violation():
    result = evaluate_trajectory(
        trajectory(windows={"relaxation": [8, 8]}),
        [
            turn(8, intervention="offer_relaxation", offers=[RELAX_OFFER]),
            turn(9, intervention="continue_interview", declined_kind="relaxation"),
            turn(10, intervention="offer_relaxation", offers=[RELAX_OFFER]),
        ],
    )
    assert result["repeat_within_cooldown"] == 1
    assert result["repeat_after_cooldown"] == 0


def test_repeating_an_offer_after_the_cooldown_is_recorded_separately():
    result = evaluate_trajectory(
        trajectory(windows={"relaxation": [8, 8]}),
        [
            turn(8, intervention="offer_relaxation", offers=[RELAX_OFFER]),
            turn(9, intervention="continue_interview", declined_kind="relaxation"),
            turn(14, intervention="offer_relaxation", offers=[RELAX_OFFER]),
        ],
    )
    assert result["repeat_within_cooldown"] == 0
    assert result["repeat_after_cooldown"] == 1


def test_offering_during_a_running_scale_is_inappropriate():
    result = evaluate_trajectory(
        trajectory(windows={}),
        [turn(6, intervention="offer_scale", offers=[SCALE_OFFER], active_scale="PHQ-9")],
    )
    assert result["inappropriate_offers"] == 1


def test_offering_while_the_participant_wants_to_talk_is_inappropriate():
    result = evaluate_trajectory(
        trajectory(windows={}),
        [
            turn(
                6,
                intervention="offer_relaxation",
                offers=[RELAX_OFFER],
                listening=True,
                expect="continue_interview",
            )
        ],
    )
    assert result["inappropriate_offers"] == 1


def test_scale_choice_is_graded_against_the_annotated_instrument():
    good = evaluate_trajectory(
        trajectory(gold_scale="PHQ-9", windows={"scale": [6, 6]}),
        [turn(6, intervention="offer_scale", offers=[SCALE_OFFER])],
    )
    bad = evaluate_trajectory(
        trajectory(gold_scale="GAD-7", windows={"scale": [6, 6]}),
        [turn(6, intervention="offer_scale", offers=[SCALE_OFFER])],
    )
    assert good["scale_choice_correct"] is True
    assert bad["scale_choice_correct"] is False


def test_execution_is_only_successful_when_the_accepted_scale_actually_started():
    ok = evaluate_trajectory(
        trajectory(windows={}),
        [
            turn(
                7,
                executed="start_scale",
                active_scale="PHQ-9",
                accepted_offer_kind="scale",
                accepted_offer_target="PHQ-9",
            )
        ],
    )
    not_started = evaluate_trajectory(
        trajectory(windows={}),
        [
            turn(
                7,
                executed="chat",
                active_scale=None,
                accepted_offer_kind="scale",
                accepted_offer_target="PHQ-9",
            )
        ],
    )
    assert ok["execution_success"] is True
    assert not_started["execution_success"] is False


def test_an_executed_proposal_is_not_counted_as_blocked():
    result = evaluate_trajectory(
        trajectory(windows={}),
        [
            turn(
                6,
                intervention="offer_scale",
                offers=[],
                executed="start_scale",
                active_scale="PHQ-9",
            )
        ],
    )
    assert result["offer_proposed"] == 1
    assert result["offer_blocked"] == 0


def test_a_turn_that_resolves_a_pending_offer_is_not_counted_as_blocked():
    """An acceptance turn carries validate_reason="offer_already_pending"
    because an invitation is open, but it consummates the invitation instead
    of refusing one; counting it as blocked misattributed 3 of the holdout
    run's 62 proposals."""
    result = evaluate_trajectory(
        trajectory(windows={}),
        [
            turn(
                9,
                intervention="offer_relaxation",
                offers=[],
                validate_reason="offer_already_pending",
                executed="recommend_relaxation",
                accepted_offer_kind="relaxation",
            )
        ],
    )
    assert result["offer_proposed"] == 1
    assert result["offer_blocked"] == 0


def test_a_rejected_proposal_is_counted_as_blocked():
    result = evaluate_trajectory(
        trajectory(windows={}),
        [
            turn(
                6,
                intervention="offer_scale",
                offers=[],
                validate_reason="risk_requires_safety_path",
            )
        ],
    )
    assert result["offer_blocked"] == 1


def test_aggregate_reports_rates_and_pooled_counts():
    rows = [
        evaluate_trajectory(
            trajectory(id="A", windows={"scale": [6, 6]}),
            [turn(6, intervention="offer_scale", offers=[SCALE_OFFER])],
        ),
        evaluate_trajectory(
            trajectory(id="B", windows={"scale": [6, 6]}), [turn(6), turn(7)]
        ),
    ]
    summary = aggregate(rows)
    assert summary["trajectories"] == 2
    assert summary["scale"]["opportunities"] == 2
    assert summary["scale"]["hit_rate"] == 0.5
    assert summary["scale"]["miss_rate"] == 0.5
    assert summary["relaxation"]["opportunities"] == 0
    assert summary["relaxation"]["hit_rate"] is None


def test_report_block_names_every_headline_metric():
    summary = aggregate(
        [
            evaluate_trajectory(
                trajectory(id="A", windows={"scale": [6, 6]}),
                [turn(6, intervention="offer_scale", offers=[SCALE_OFFER])],
            )
        ]
    )
    report = format_report(summary)
    for label in ("scale", "relax", "guards", "quality", "scale_choice", "policy_agreement"):
        assert label in report


# ---------------------------------------------------------------------------
# Multi-window and uncertain annotations
# ---------------------------------------------------------------------------


def test_offering_inside_any_acceptable_window_is_a_hit():
    result = evaluate_trajectory(
        trajectory(windows={"scale": [[5, 6], [9, 10]]}),
        [turn(4), turn(9, intervention="offer_scale", offers=[SCALE_OFFER])],
    )
    assert result["scale_outcome"] == "hit"
    assert result["scale_windows"] == [[5, 6], [9, 10]]


def test_offering_between_two_windows_is_late_not_spurious():
    result = evaluate_trajectory(
        trajectory(windows={"scale": [[5, 6], [10, 11]]}),
        [turn(8, intervention="offer_scale", offers=[SCALE_OFFER])],
    )
    assert result["scale_outcome"] == "late"


def test_a_late_offer_reports_both_delays():
    result = evaluate_trajectory(
        trajectory(windows={"scale": [6, 8]}),
        [turn(10, intervention="offer_scale", offers=[SCALE_OFFER])],
    )
    # delay keeps the historical definition (vs window start); late_by is the
    # stricter reading (vs window end)
    assert result["scale_delay"] == 4
    assert result["scale_late_by"] == 2


def test_an_uncertain_window_is_reported_but_not_scored():
    certain = evaluate_trajectory(
        trajectory(id="C", windows={"scale": [6, 7]}),
        [turn(6), turn(7)],
    )
    uncertain = evaluate_trajectory(
        trajectory(id="U", windows={"scale": {"any": [[6, 7]], "certainty": "uncertain"}}),
        [turn(6), turn(7)],
    )
    assert certain["scale_scored"] is True
    assert uncertain["scale_scored"] is False
    assert uncertain["scale_certainty"] == "uncertain"

    summary = aggregate([certain, uncertain])
    assert summary["scale"]["opportunities"] == 1
    assert summary["scale"]["uncertain_opportunities"] == 1


# ---------------------------------------------------------------------------
# Micro vs macro
# ---------------------------------------------------------------------------


def test_micro_and_macro_are_reported_separately_and_can_differ():
    long_row = evaluate_trajectory(
        trajectory(id="LONG", windows={}),
        [
            turn(1, expect="continue_interview"),
            turn(2, expect="continue_interview"),
            turn(3, expect="continue_interview"),
            turn(4, intervention="offer_scale", expect="offer_scale"),
        ],
    )
    short_row = evaluate_trajectory(
        trajectory(id="SHORT", windows={}),
        [turn(1, expect="offer_scale")],
    )
    summary = aggregate([long_row, short_row])

    # micro: 4 of 5 graded turns agreed
    assert summary["micro"]["turn_agreement"] == 0.8
    assert summary["micro"]["turns_graded"] == 5
    # macro: mean of (1.0, 0.0)
    assert summary["macro"]["turn_agreement"] == 0.5


# ---------------------------------------------------------------------------
# Permission violations come from events, not from reason strings
# ---------------------------------------------------------------------------


def test_execution_with_an_accepted_offer_id_is_not_a_violation():
    result = evaluate_trajectory(
        trajectory(windows={}),
        [
            turn(
                7,
                executed="start_scale",
                active_scale="PHQ-9",
                accepted_offer_kind="scale",
                accepted_offer_target="PHQ-9",
                accepted_offer_id="scale-7-1",
            )
        ],
    )
    assert result["permission_violations"] == 0


def test_execution_after_an_explicit_request_is_not_a_violation():
    result = evaluate_trajectory(
        trajectory(windows={}),
        [turn(7, executed="start_scale", active_scale="PHQ-9", explicit_request="scale")],
    )
    assert result["permission_violations"] == 0


def test_execution_with_neither_evidence_is_a_violation_even_with_a_consent_reason():
    """The reason string must not be able to authorise anything on its own."""
    result = evaluate_trajectory(
        trajectory(windows={}),
        [
            turn(
                7,
                executed="start_scale",
                active_scale="PHQ-9",
                policy_reason="offer_scale_accepted",
                consent_basis="offer_accepted",
            )
        ],
    )
    assert result["permission_violations"] == 1


def test_recommending_relaxation_without_consent_is_a_violation():
    result = evaluate_trajectory(
        trajectory(windows={}),
        [turn(8, executed="recommend_relaxation")],
    )
    assert result["permission_violations"] == 1


# ---------------------------------------------------------------------------
# Stage separation
# ---------------------------------------------------------------------------


def test_the_four_stages_are_counted_apart():
    from decision.timing_metrics import aggregate_turns

    turns = {
        "T": [
            turn(1, intervention="offer_scale", validate_reason="risk_requires_safety_path"),
            turn(2, intervention="offer_scale", validate_accepted=True, offers=[SCALE_OFFER]),
            turn(3, intervention="continue_interview", validate_accepted=True),
        ]
    }
    stage = aggregate_turns([], turns)
    assert stage["proposed"]["offer_scale"] == 2
    assert stage["validator"] == {"accepted": 2, "rejected": 1}
    assert stage["reject_reasons"]["risk_requires_safety_path"] == 1
    assert stage["policy_actions"]["chat"] == 3
