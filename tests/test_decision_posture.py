"""Posture: the decision layer's answer to "what does this turn do".

The intervention question only decides whether the interview changes task.
On a chat turn that answer is always "keep going", so the decision layer used
to contribute nothing but a boolean.  These tests pin the two questions that
give "keep going" a meaning, and the rules that keep their combination
coherent.
"""

import pytest

from conversation.contracts import TurnAction
from decision.contracts import RawDecision, build_observation
from decision.posture import (
    STANCE_LABELS,
    STANCE_OPTIONS,
    STANCE_OPTION_TEXTS,
    STRATEGY_LABELS,
    normalize_stance,
    normalize_strategy,
    resolve_posture,
)


def _chat(strategy=None, stance=None, **overrides):
    raw = RawDecision(
        provider="fake",
        intervention="continue_interview",
        strategy=strategy,
        stance=stance,
        confidence=0.6,
        **overrides,
    )
    return build_observation(raw, source="jev").proposal


# --- normalisation ---------------------------------------------------------

@pytest.mark.parametrize("label", STRATEGY_LABELS)
def test_strategy_label_round_trips(label):
    assert normalize_strategy(label) == label


@pytest.mark.parametrize("label", STANCE_LABELS)
def test_stance_label_round_trips(label):
    assert normalize_stance(label) == label


def test_option_text_is_accepted_as_an_answer():
    """Providers return option text, not labels; both must resolve."""
    assert normalize_strategy("Bring this topic to a close") == "close"
    assert normalize_stance("Acknowledge what was said without asking anything") == "reflect"


def test_unknown_answer_is_dropped_not_raised():
    """An auxiliary answer must never invalidate the primary decision."""
    assert normalize_strategy("something the model invented") is None
    assert normalize_stance("") is None
    assert normalize_strategy(None) is None


# --- coherence -------------------------------------------------------------

@pytest.mark.parametrize("strategy,stance,expected", [
    ("deepen", "probe", ("deepen", "probe")),
    # Opening or deepening asks by nature: an acknowledgement with no question
    # to spend the topic on produced a truism or an empty completion.
    ("explore", "reflect", ("explore", "probe")),
    ("deepen", "reflect", ("deepen", "probe")),
    # A hold is the move that genuinely may not ask.
    ("hold", "reflect", ("hold", "reflect")),
    # A hold that ends in a fresh question is not a hold.
    ("hold", "probe", ("hold", "reflect")),
    ("close", "probe", ("close", "summarize")),
    # A close that only acknowledges leaves the topic hanging.
    ("close", "reflect", ("close", "summarize")),
    # Opening a topic and summarising it in the same breath cancels out.
    ("explore", "summarize", ("explore", "probe")),
    # "Just answer briefly" outranks any move.
    ("deepen", "quiet", (None, "quiet")),
    (None, None, (None, None)),
])
def test_posture_pairs_are_reconciled(strategy, stance, expected):
    assert resolve_posture(strategy, stance) == expected


# --- boundary to the proposal ---------------------------------------------

def test_posture_travels_to_the_proposal():
    proposal = _chat("deepen", "probe")

    assert proposal.strategy == "deepen"
    assert proposal.stance == "probe"


def test_incoherent_pair_is_already_repaired_at_the_boundary():
    proposal = _chat("hold", "probe")

    assert proposal.strategy == "hold"
    assert proposal.stance == "reflect"


def test_task_turn_drops_posture():
    """A scale/relaxation/clarify turn is owned by its action.

    A stray "go deeper" would fight the action's own wording instructions.
    """
    raw = RawDecision(
        provider="fake",
        intervention="offer_scale",
        offer_target="PHQ-9",
        strategy="deepen",
        stance="probe",
        confidence=0.8,
    )
    proposal = build_observation(raw, source="jev").proposal

    assert proposal.action.value == "start_scale"
    assert proposal.strategy is None
    assert proposal.stance is None


def test_clarify_turn_drops_posture():
    raw = RawDecision(
        provider="fake",
        intervention="clarify_first",
        strategy="deepen",
        stance="probe",
        confidence=0.8,
    )
    proposal = build_observation(raw, source="jev").proposal

    assert proposal.strategy is None
    assert proposal.stance is None


def test_policy_carries_posture_onto_the_decision():
    from conversation.turn_policy import TurnPolicy

    decision = TurnPolicy()._chat("router_default", _chat("explore", "probe"))

    assert decision.action is TurnAction.CHAT
    assert decision.strategy == "explore"
    assert decision.stance == "probe"


def test_answer_briefly_is_no_longer_offered_as_a_stance():
    """It was the fourth option and the model's favourite: on real intake turns
    it answered "answer briefly and leave the space open" twice out of six,
    which nulls the strategy *and* the focus, so the turn carried no direction
    at all.  "Acknowledge without asking" already covers the intent."""
    assert "quiet" not in STANCE_OPTION_TEXTS
    assert len(STANCE_OPTIONS) == 3


def test_a_legacy_quiet_answer_is_still_reconciled():
    """Replaying an old decision (or a provider that still emits the label)
    must not raise: the vocabulary is offered, not enforced."""
    assert resolve_posture("deepen", "quiet") == (None, "quiet")
