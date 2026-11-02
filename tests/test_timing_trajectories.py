"""The multi-turn trajectory set and the guarantees it has to hold.

Two things are tested here, and they are deliberately different:

1. **The chain can reach an annotated window at all.**  A replay provider that
   is told exactly what to do must hit every window; if it cannot, the contracts,
   validator, policy, ledger or runtime is broken and no model score below is
   meaningful.
2. **The guards hold for every provider.**  An invitation never appears on a
   turn that is inside a running scale, inside a crisis disclosure, or after an
   explicit request to stop -- regardless of what the decision backend wanted.

What is *not* tested here is whether a model chooses well.  That is what the
benchmark reports, and it needs the real checkpoints.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import MIN_ROUNDS_BEFORE_SCALE, MIN_ROUNDS_FOR_RELAXATION
from decision.providers.rule import RuleTimingProvider
from decision.providers.scripted import ScriptedTimingProvider
from decision.timing import INTERVENTION_LABELS
from decision.timing_eval import run_trajectories

CASES = Path(__file__).resolve().parents[1] / "scripts" / "benchmarks" / "cases"
DEV = CASES / "interview_trajectories_dev.jsonl"
TEST = CASES / "interview_trajectories_test.jsonl"


def load(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            rows.append(json.loads(text))
    return rows


@pytest.fixture(scope="module")
def dev():
    return load(DEV)


@pytest.fixture(scope="module")
def test_split():
    return load(TEST)


# ---------------------------------------------------------------------------
# The case set itself
# ---------------------------------------------------------------------------


def test_the_two_splits_cover_at_least_fourteen_distinct_scenarios(dev, test_split):
    # The requirement is a floor, not an exact count: new scenarios may be added
    # to either split, but neither may shrink below the agreed coverage.
    assert len(dev) >= 6
    assert len(test_split) >= 8
    ids = [row["id"] for row in dev + test_split]
    assert len(set(ids)) == len(ids)
    assert len(ids) >= 14


def test_every_split_is_disjoint(dev, test_split):
    titles = {row["title"] for row in dev} & {row["title"] for row in test_split}
    assert titles == set()


def test_every_trajectory_has_rounds_in_order_and_a_text_per_turn(dev, test_split):
    for row in dev + test_split:
        rounds = [turn.get("round") for turn in row["turns"]]
        assert rounds == sorted(rounds), row["id"]
        assert len(row["turns"]) >= 3, row["id"]
        for turn in row["turns"]:
            assert turn.get("text"), row["id"]
            if turn.get("expect"):
                assert turn["expect"] in INTERVENTION_LABELS, (row["id"], turn["expect"])


def test_no_window_opens_before_the_programme_floors(dev, test_split):
    # A window that starts below the floor would ask the policy to do something
    # its own rules forbid, which would make the metric meaningless.
    for row in dev + test_split:
        windows = row.get("windows") or {}
        if "scale" in windows:
            assert windows["scale"][0] >= MIN_ROUNDS_BEFORE_SCALE, row["id"]
        if "relaxation" in windows:
            assert windows["relaxation"][0] >= MIN_ROUNDS_FOR_RELAXATION, row["id"]


# ---------------------------------------------------------------------------
# The chain must be able to succeed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("split_name", ["dev", "test"])
def test_replay_reaches_every_annotated_window(split_name):
    rows = load(DEV if split_name == "dev" else TEST)
    report = run_trajectories(
        provider_for=ScriptedTimingProvider.from_trajectory, trajectories=rows
    )
    summary = report["summary"]
    for kind in ("scale", "relaxation"):
        entry = summary[kind]
        if entry["opportunities"]:
            assert entry["hit_rate"] == 1.0, (split_name, kind, entry)
        assert entry["spurious_offers"] == 0, (split_name, kind)
    assert summary["repeat_within_cooldown"] == 0
    assert summary["inappropriate_offers"] == 0
    assert summary["turn_agreement"] == 1.0
    assert summary["policy_agreement"] == 1.0


@pytest.mark.parametrize("split_name", ["dev", "test"])
def test_replay_starts_a_scale_only_through_an_approved_route(split_name):
    rows = load(DEV if split_name == "dev" else TEST)
    report = run_trajectories(
        provider_for=ScriptedTimingProvider.from_trajectory, trajectories=rows
    )
    approved = {
        "offer_scale_accepted",     # the participant said yes
        "user_scale_request",       # the participant asked first
        "deterministic_scale_signal",
        "router_start_scale_accepted",
        "router_deterministic_agreement",
    }
    for trajectory in report["trajectories"]:
        consented = False
        for turn in trajectory["turns"]:
            if turn["accepted_offer_kind"] == "scale":
                consented = True
            if turn["executed"] == "start_scale":
                assert turn["policy_reason"] in approved, (trajectory["id"], turn["round"])
                if turn["policy_reason"] == "offer_scale_accepted":
                    assert consented, (trajectory["id"], turn["round"])


# ---------------------------------------------------------------------------
# Guards hold for every provider
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("split_name", ["dev", "test"])
def test_no_invitation_is_recorded_during_a_running_scale(split_name):
    rows = load(DEV if split_name == "dev" else TEST)
    report = run_trajectories(provider=RuleTimingProvider(), trajectories=rows)
    for trajectory in report["trajectories"]:
        for turn in trajectory["turns"]:
            if turn.get("active_scale"):
                assert turn["offers"] == [], (trajectory["id"], turn["round"])


@pytest.mark.parametrize("split_name", ["dev", "test"])
def test_no_invitation_is_recorded_on_a_crisis_turn(split_name):
    rows = load(DEV if split_name == "dev" else TEST)
    report = run_trajectories(provider=RuleTimingProvider(), trajectories=rows)
    for trajectory in report["trajectories"]:
        for turn in trajectory["turns"]:
            if turn.get("validate_reason") == "risk_requires_safety_path":
                assert turn["offers"] == []


def test_an_explicit_request_to_stop_always_ends_the_session(test_split):
    report = run_trajectories(provider=RuleTimingProvider(), trajectories=test_split)
    ending = [
        turn
        for trajectory in report["trajectories"]
        for turn in trajectory["turns"]
        if turn.get("expect_action") == "end_session"
    ]
    assert ending, "the case set must contain an explicit ending"
    for turn in ending:
        assert turn["policy_action"] == "end_session"
        assert turn["policy_reason"] == "explicit_end"


def test_the_rule_baseline_never_starts_an_unregistered_scale(dev, test_split):
    from services.scales import SCALES

    report = run_trajectories(provider=RuleTimingProvider(), trajectories=dev + test_split)
    for trajectory in report["trajectories"]:
        for turn in trajectory["turns"]:
            if turn["active_scale"]:
                assert turn["active_scale"] in SCALES
            for offer in turn["offers"]:
                if offer["kind"] == "scale":
                    assert offer["target"] in SCALES


def test_the_rule_baseline_is_reported_as_a_baseline_not_a_model():
    provider = RuleTimingProvider()
    assert provider.name == "rule_baseline"
    assert provider.is_available() is True
    assert provider.describe()["min_rounds_before_scale"] == MIN_ROUNDS_BEFORE_SCALE


# ---------------------------------------------------------------------------
# The holdout set: frozen, unseen, and structurally sound
# ---------------------------------------------------------------------------

HOLDOUT = CASES / "interview_trajectories_holdout.jsonl"


@pytest.fixture(scope="module")
def holdout():
    return load(HOLDOUT)


def test_the_holdout_does_not_reuse_ids_from_the_other_splits(dev, test_split, holdout):
    others = {row["id"] for row in dev} | {row["id"] for row in test_split}
    assert {row["id"] for row in holdout}.isdisjoint(others)
    assert all(row["split"] == "holdout" for row in holdout)


def test_every_holdout_window_is_satisfiable_by_the_programme_floors(holdout):
    """An annotation the floors forbid is an annotation no system can meet."""
    from decision.timing_metrics import _normalise_windows

    floors = {"scale": MIN_ROUNDS_BEFORE_SCALE, "relaxation": MIN_ROUNDS_FOR_RELAXATION}
    for row in holdout:
        for kind, floor in floors.items():
            windows, _certainty = _normalise_windows((row.get("windows") or {}).get(kind))
            for start, _end in windows:
                assert start >= floor, f"{row['id']} {kind} window starts at {start} < floor {floor}"


def test_holdout_rounds_are_sequential(holdout):
    for row in holdout:
        rounds = [turn.get("round") or index for index, turn in enumerate(row["turns"], start=1)]
        assert rounds == list(range(1, len(rounds) + 1)), row["id"]


def test_the_holdout_covers_the_required_scenarios(holdout):
    from decision.timing_metrics import _normalise_windows

    windows = [
        {
            kind: _normalise_windows((row.get("windows") or {}).get(kind))
            for kind in ("scale", "relaxation")
        }
        for row in holdout
    ]

    def kinds_of(entry):
        return {kind for kind, (spans, _c) in entry.items() if spans}

    assert any(kinds_of(entry) == {"scale"} for entry in windows)
    assert any(kinds_of(entry) == {"relaxation"} for entry in windows)
    assert any(kinds_of(entry) == set() for entry in windows)  # must not offer
    assert any(
        any(certainty == "uncertain" for _spans, certainty in entry.values())
        for entry in windows
    )
    assert any(
        any(len(spans) > 1 for spans, _c in entry.values()) for entry in windows
    )
    # a refusal followed by a later acceptable window
    assert any(
        row.get("windows", {}).get("scale") and any(
            "不用" in str(turn.get("text") or "") or "先别" in str(turn.get("text") or "")
            for turn in row["turns"]
        )
        for row in holdout
    )


def test_a_turn_inside_a_window_carries_no_per_turn_expectation(holdout):
    """A range has no single correct round; annotating one would penalise the rest."""
    from decision.timing_metrics import _normalise_windows

    for row in holdout:
        for kind in ("scale", "relaxation"):
            spans, _c = _normalise_windows((row.get("windows") or {}).get(kind))
            for turn in row["turns"]:
                round_number = turn["round"]
                if any(start <= round_number <= end for start, end in spans):
                    assert "expect" not in turn, f"{row['id']} round {round_number}"
