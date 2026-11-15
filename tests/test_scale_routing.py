"""Scale routing: the instrument must follow the participant's words.

The bug these tests defend against was not a crash.  Every session, whatever
the participant described, ran PHQ-9, because four separate places either
hard-coded it or fell back to list position:

* ``turn_policy`` took ``scale_candidates[0]`` for an explicit request;
* ``rank_scale_candidates`` ordered only by registry index;
* the routing table gave PHQ-9 three bonuses and shared its colloquial
  keywords ("坐不住", "烦躁") with GAD-7 as well;
* ``should_administer``'s round-count fallback returned ``"PHQ-9"`` outright.

The result was a data problem, not a cosmetic one: anxiety and trauma
dimensions were never measured, so a study design that needs to separate chief
complaints had nothing to separate.
"""

from __future__ import annotations

import pytest

from conversation.contracts import TurnAction, TurnStateSnapshot
from conversation.turn_policy import TurnPolicy
from conversation.turn_signals import collect_turn_signals
from core.scoring import (
    SCALE_EVIDENCE_THRESHOLD,
    has_trauma_event,
    primary_scale_from_scores,
    score_symptom_signals,
)
from decision.request_builder import build_decision_request
from decision.providers.base import build_state_text
from decision.timing import (
    SCALE_REASON_EVIDENCE,
    SCALE_REASON_INCOMPLETE,
    rank_scale_candidates,
)

REGISTRY = ("PHQ-9", "GAD-7", "PCL-5")


def ranked(text: str, **kwargs):
    deltas, _ = score_symptom_signals(text)
    return rank_scale_candidates(registered=REGISTRY, symptom_scores=deltas, **kwargs)


# ---------------------------------------------------------------------------
# The routing table
# ---------------------------------------------------------------------------


def test_a_depressive_complaint_points_at_phq9():
    deltas, _ = score_symptom_signals("心里很难受，什么都不想干，晚上总睡不着")
    assert primary_scale_from_scores(deltas) == "PHQ-9"


def test_an_anxious_complaint_points_at_gad7():
    deltas, _ = score_symptom_signals("最近总是很紧张，控制不住地担心，坐立不安")
    best = primary_scale_from_scores(deltas)
    assert best == "GAD-7", deltas
    # Not a photo finish: the anxiety evidence must clearly outrank the
    # agitation residue that PHQ-9 item 8 legitimately also owns.
    assert deltas["GAD-7"] >= deltas["PHQ-9"] + 3


def test_a_trauma_complaint_points_at_pcl5():
    deltas, _ = score_symptom_signals("车祸之后老做噩梦，一有动静就吓一跳，不敢想那件事")
    assert primary_scale_from_scores(deltas) == "PCL-5", deltas


def test_trauma_symptoms_without_an_event_do_not_route_to_pcl5():
    """Criterion A is a gate, not a bonus.

    "我最近注意力不集中、容易受惊" is a description of anxiety or poor sleep.
    Without a named index event it must not credit PCL-5 at all, or every tired
    and jumpy participant gets screened for PTSD.
    """
    text = "最近睡不好，注意力也不集中，一点动静就吓一跳"
    deltas, _ = score_symptom_signals(text)
    assert deltas["PCL-5"] == 0
    assert has_trauma_event(text) is False


def test_an_ordinary_loss_is_not_a_trauma_event():
    """Breakups and job loss are common and painful, but not criterion A."""
    for text in ("最近跟男朋友分手了，很难受", "上个月被裁员了，压力很大"):
        deltas, _ = score_symptom_signals(text)
        assert deltas["PCL-5"] == 0, text


def test_no_evidence_nominates_nothing():
    """Zero is absence of evidence, not a weak preference for the first scale."""
    assert primary_scale_from_scores({}) is None
    assert primary_scale_from_scores({"PHQ-9": 0, "GAD-7": 0, "PCL-5": 0}) is None


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_an_anxious_participant_is_offered_gad7_first():
    order = [candidate.name for candidate in ranked("最近很焦虑，控制不住地担心")]
    assert order[0] == "GAD-7", order
    assert order[-1] != "GAD-7"


def test_ranking_marks_evidence_and_plain_registry_order_differently():
    evidenced = ranked("最近很焦虑，控制不住地担心")[0]
    assert evidenced.reason == SCALE_REASON_EVIDENCE
    assert evidenced.evidence > 0

    plain = ranked("今天天气不错")
    assert all(candidate.reason != SCALE_REASON_EVIDENCE for candidate in plain)
    # With nothing said yet, the registry order is the only honest order.
    assert [candidate.name for candidate in plain] == list(REGISTRY)
    assert all(candidate.evidence == 0 for candidate in plain)


def test_an_unfinished_instrument_is_resumed_before_opening_a_new_one():
    candidates = rank_scale_candidates(
        registered=REGISTRY,
        administered=("PCL-5",),
        symptom_scores={"PHQ-9": 9, "GAD-7": 0, "PCL-5": 0},
    )
    assert candidates[0].name == "PCL-5"
    assert candidates[0].reason == SCALE_REASON_INCOMPLETE


def test_completed_and_declined_instruments_are_not_offered_again():
    candidates = rank_scale_candidates(
        registered=REGISTRY,
        completed=("PHQ-9",),
        declined=("GAD-7",),
    )
    assert [candidate.name for candidate in candidates] == ["PCL-5"]


# ---------------------------------------------------------------------------
# The policy's explicit-request path
# ---------------------------------------------------------------------------


def _snapshot(**overrides) -> TurnStateSnapshot:
    values = {
        "session_state": "CHATTING",
        "round_count": 6,
        "active_scale": None,
        "scale_candidates": ("PHQ-9", "GAD-7", "PCL-5"),
        "scale_candidate_evidence": {},
        "risk_marker": False,
    }
    values.update(overrides)
    return TurnStateSnapshot(**values)


def _decide(text: str, state: TurnStateSnapshot):
    signals = collect_turn_signals(text, state)
    assert signals.explicit_scale_requested, "the test text must read as a request"
    return TurnPolicy().decide(
        user_text=text,
        proposal=None,
        snapshot=state,
        signals=signals,
    )


def test_an_explicit_request_follows_evidence_even_when_the_list_is_unsorted():
    """The list position must not decide the instrument.

    ``scale_candidate_evidence`` is the authority; a caller that hands over an
    unordered tuple must still get the matching instrument.
    """
    state = _snapshot(
        scale_candidates=("PHQ-9", "GAD-7", "PCL-5"),
        scale_candidate_evidence={"PHQ-9": 1, "GAD-7": 8, "PCL-5": 0},
    )
    decision = _decide("我能做个问卷吗？", state)
    assert decision.action is TurnAction.START_SCALE
    assert decision.scale_name == "GAD-7", decision.scale_name


def test_an_explicit_request_without_evidence_falls_back_to_the_first_candidate():
    state = _snapshot(scale_candidates=("GAD-7", "PHQ-9"))
    decision = _decide("我能做个问卷吗？", state)
    assert decision.scale_name == "GAD-7"


def test_no_candidate_still_degrades_to_chat():
    state = _snapshot(scale_candidates=())
    decision = _decide("我能做个问卷吗？", state)
    assert decision.action is not TurnAction.START_SCALE
    assert decision.reason == "user_scale_request_no_candidate"


# ---------------------------------------------------------------------------
# What the decision model is told
# ---------------------------------------------------------------------------


def test_the_state_block_explains_why_the_first_candidate_is_first():
    request = build_decision_request(
        user_text="最近很焦虑，控制不住地担心",
        round_count=6,
        registered_scales=REGISTRY,
        symptom_scores={"PHQ-9": 2, "GAD-7": 8, "PCL-5": 0},
    )
    state_text = build_state_text(request)
    assert "GAD-7" in state_text
    assert "谈话内容更吻合" in state_text
    assert "从高到低" in state_text
    # The order itself has to be right, not just the annotation.
    assert state_text.index("GAD-7") < state_text.index("PHQ-9")


# ---------------------------------------------------------------------------
# The trauma gate is per participant, not per sentence
# ---------------------------------------------------------------------------


def test_symptoms_after_a_named_event_keep_scoring():
    """A real session showed the gate closing one turn too early.

    Turn 1 named the car crash (+3, enough to route).  The three turns after it
    described nightmares, avoidance and hyperarousal, and every one of them
    scored zero, because the marker "车祸" was not repeated.  A participant says
    the event once and then talks about its aftermath -- the gate has to follow
    the participant's history, not the sentence.
    """
    scores = {"PHQ-9": 0, "GAD-7": 0, "PCL-5": 0}
    first, _ = score_symptom_signals("去年出过一次车祸，我坐在副驾驶。", scores)
    for scale, delta in first.items():
        scores[scale] += delta
    assert scores["PCL-5"] >= SCALE_EVIDENCE_THRESHOLD

    follow_up = [
        "晚上总做噩梦，梦见又撞了一次。",
        "现在不敢再坐车了，一上路就发紧。",
        "听到喇叭声会吓一跳，整个人一下就绷起来。",
    ]
    for text in follow_up:
        deltas, _reasons = score_symptom_signals(text, scores)
        assert deltas["PCL-5"] > 0, text
        for scale, delta in deltas.items():
            scores[scale] += delta

    assert primary_scale_from_scores(scores) == "PCL-5"


def test_the_gate_still_blocks_when_no_event_was_ever_named():
    """Widening the gate must not open it for everyone.

    Without a prior event, the same symptom sentences may not credit PCL-5 at
    all -- otherwise a bad night's sleep routes an ordinary participant into a
    PTSD screen.
    """
    for text in ("晚上总做噩梦，梦见又撞了一次。", "听到喇叭声会吓一跳。"):
        deltas, _reasons = score_symptom_signals(text, {"PHQ-9": 0, "GAD-7": 0, "PCL-5": 0})
        assert deltas["PCL-5"] == 0, text
