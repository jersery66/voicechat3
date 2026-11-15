"""A withdrawn participant must not be asked "你指的是什么？" all session.

Real trace (S103): ten turns, seven of them "不知道" / "就那样" / "不想聊这个",
every one came back as CLARIFY_INPUT.  The router reads vague answers as
ambiguous, and the clarification branch sits above both the questionnaire and
the exercise invitation -- so the two things the programme guarantees were
unreachable.  The cap keeps clarification available for one misunderstanding
without letting it own the session.
"""

from __future__ import annotations

from config import MAX_CONSECUTIVE_CLARIFICATIONS
from conversation.contracts import (
    RouterAction,
    RouterProposal,
    TurnSignals,
    TurnStateSnapshot,
)
from conversation.turn_policy import TurnPolicy


def _decide(text, *, snapshot, proposal, signals=None):
    return TurnPolicy().decide(
        user_text=text,
        proposal=proposal,
        snapshot=snapshot,
        signals=signals or TurnSignals(),
    )


def _clarify_proposal():
    return RouterProposal(
        action=RouterAction.CHAT,
        clarify=True,
        confidence=0.4,
        reason="router_clarify_first",
    )


def test_first_ambiguous_turn_is_still_clarified():
    decision = _decide(
        "这个也说不上来。",
        snapshot=TurnStateSnapshot(round_count=3, clarifications_in_a_row=0),
        proposal=_clarify_proposal(),
    )
    assert decision.action.value == "clarify_input"


def test_clarifying_stops_after_the_cap():
    decision = _decide(
        "这个也说不上来。",
        snapshot=TurnStateSnapshot(
            round_count=6, clarifications_in_a_row=MAX_CONSECUTIVE_CLARIFICATIONS
        ),
        proposal=_clarify_proposal(),
    )
    assert decision.action.value != "clarify_input"


def test_capped_session_still_reaches_the_guaranteed_exercise():
    """The invitation must outrank a clarification that has run out of tries."""
    decision = _decide(
        "随便。",
        snapshot=TurnStateSnapshot(
            round_count=9,
            clarifications_in_a_row=MAX_CONSECUTIVE_CLARIFICATIONS,
        ),
        proposal=_clarify_proposal(),
        signals=TurnSignals(proactive_relaxation_candidate="breathing"),
    )
    assert decision.action.value == "recommend_relaxation"
    assert decision.reason == "proactive_relaxation_accepted"


def test_semantic_ambiguity_signal_is_capped_too():
    """Both clarify branches share the cap; neither may outlive it."""
    decision = _decide(
        "嗯。",
        snapshot=TurnStateSnapshot(
            round_count=7, clarifications_in_a_row=MAX_CONSECUTIVE_CLARIFICATIONS
        ),
        proposal=RouterProposal(action=RouterAction.CHAT, confidence=0.9),
        signals=TurnSignals(semantic_ambiguity=True, semantic_target="emotion"),
    )
    assert decision.action.value != "clarify_input"
