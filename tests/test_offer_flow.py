"""The invitation lifecycle, end to end through ``TurnPolicy``.

The requirement these tests defend is narrow and non-negotiable:

* a suggestion is not an execution;
* a refusal survives the next turn;
* consent is never inferred from a sentence that merely contains "可以";
* a safety marker or an explicit request to stop outranks any activity plan.

None of this is model behaviour -- the policy is a pure function -- so the tests
call it directly instead of pretending a benchmark proves it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config import MIN_ROUNDS_BEFORE_SCALE, MIN_ROUNDS_FOR_RELAXATION
from conversation.contracts import (
    RouterAction,
    RouterProposal,
    TurnAction,
    TurnSignals,
    TurnStateSnapshot,
)
from conversation.turn_policy import TurnPolicy
from conversation.turn_signals import collect_turn_signals
from decision.timing import (
    OFFER_REASON_ANSWER_AMBIGUOUS,
    OFFER_REASON_RELAXATION_ACCEPTED,
    OFFER_REASON_RELAXATION_DECLINED,
    OFFER_REASON_SCALE_ACCEPTED,
    OFFER_REASON_SCALE_DECLINED,
)


def snapshot(**overrides) -> TurnStateSnapshot:
    values = {
        "session_state": "CHATTING",
        "round_count": MIN_ROUNDS_BEFORE_SCALE,
        "active_scale": None,
        "current_item": None,
        "waiting_for_answer": False,
        "completed_scales": (),
        "relaxation_used": False,
        "proactive_relaxation_offered": False,
        "game_active": False,
        "time_limit_reached": False,
        "scale_candidates": ("PHQ-9", "GAD-7"),
        "risk_marker": False,
    }
    values.update(overrides)
    return TurnStateSnapshot(**values)


def scale_offer_proposal(scale="PHQ-9", confidence=0.9) -> RouterProposal:
    return RouterProposal(
        action=RouterAction.START_SCALE,
        scale_name=scale,
        offer="scale",
        confidence=confidence,
        reason="test_offer_scale",
    )


def relaxation_offer_proposal(confidence=0.9) -> RouterProposal:
    return RouterProposal(
        action=RouterAction.RECOMMEND_RELAXATION,
        offer="relaxation",
        confidence=confidence,
        reason="test_offer_relaxation",
    )


def decide(*, text="", proposal=None, state=None, signals=None, **signal_overrides):
    state = state or snapshot()
    proposal = proposal or RouterProposal.fallback("test")
    if signals is None:
        signals = TurnSignals(**signal_overrides)
    return TurnPolicy().decide(
        user_text=text,
        proposal=proposal,
        snapshot=state,
        signals=signals,
    )


# ---------------------------------------------------------------------------
# Suggestion vs execution
# ---------------------------------------------------------------------------


def test_scale_invitation_is_not_a_start():
    decision = decide(proposal=scale_offer_proposal())
    assert decision.action is TurnAction.OFFER_SCALE
    assert decision.scale_name == "PHQ-9"
    assert decision.answered_item is None
    assert decision.accepted_score is None


def test_relaxation_invitation_is_not_a_session():
    state = snapshot(round_count=MIN_ROUNDS_FOR_RELAXATION)
    decision = decide(proposal=relaxation_offer_proposal(), state=state)
    assert decision.action is TurnAction.OFFER_RELAXATION
    assert decision.intervention_type is None


def test_invitation_before_the_floor_is_refused_but_not_an_error():
    state = snapshot(round_count=MIN_ROUNDS_BEFORE_SCALE - 1)
    decision = decide(proposal=scale_offer_proposal(), state=state)
    assert decision.action is TurnAction.CHAT
    assert decision.reason == "offer_scale_before_min_rounds"


def test_invitation_without_a_target_is_refused_by_the_contract():
    # Defence in depth: the proposal contract already refuses to carry an
    # invitation with no questionnaire, so the policy's own guard is a second
    # line rather than the only one.
    with pytest.raises(ValidationError):
        RouterProposal(
            action=RouterAction.START_SCALE, offer="scale", confidence=0.9, reason="x"
        )


# ---------------------------------------------------------------------------
# Accept / decline / ambiguous
# ---------------------------------------------------------------------------


def test_accepting_a_pending_scale_invitation_starts_that_exact_scale():
    state = snapshot(pending_offer="scale", pending_offer_target="GAD-7", round_count=6)
    decision = decide(text="好，那就做吧。", state=state, offer_accepted=True)
    assert decision.action is TurnAction.START_SCALE
    assert decision.scale_name == "GAD-7"
    assert decision.reason == OFFER_REASON_SCALE_ACCEPTED


def test_declining_a_pending_scale_invitation_returns_to_the_interview():
    state = snapshot(pending_offer="scale", pending_offer_target="PHQ-9")
    decision = decide(text="不用了。", state=state, offer_declined=True)
    assert decision.action is TurnAction.CHAT
    assert decision.reason == OFFER_REASON_SCALE_DECLINED


def test_declining_a_pending_relaxation_invitation_returns_to_the_interview():
    state = snapshot(
        round_count=MIN_ROUNDS_FOR_RELAXATION,
        pending_offer="relaxation",
        relaxation_offered=True,
    )
    decision = decide(text="我现在不想做。", state=state, offer_declined=True)
    assert decision.action is TurnAction.CHAT
    assert decision.reason == OFFER_REASON_RELAXATION_DECLINED


def test_an_ambiguous_answer_asks_instead_of_assuming_consent():
    state = snapshot(pending_offer="scale", pending_offer_target="PHQ-9")
    decision = decide(
        text="嗯……",
        state=state,
        offer_answer_ambiguous=True,
        semantic_target="offer_answer",
    )
    # Ambiguity is expressed as its own flag so the branch is exercised even
    # when the classifier and the policy disagree about the wording.
    decision = TurnPolicy().decide(
        user_text="嗯……",
        proposal=RouterProposal.fallback("test"),
        snapshot=state,
        signals=TurnSignals(
            offer_accepted=False, offer_declined=False, offer_answer_ambiguous=True
        ),
    )
    assert decision.action is TurnAction.CLARIFY_INPUT
    assert decision.reason == OFFER_REASON_ANSWER_AMBIGUOUS


def test_accepting_a_relaxation_invitation_recommends_it():
    state = snapshot(
        round_count=MIN_ROUNDS_FOR_RELAXATION,
        pending_offer="relaxation",
        relaxation_offered=True,
    )
    decision = decide(text="可以，试试吧。", state=state, offer_accepted=True)
    assert decision.action is TurnAction.RECOMMEND_RELAXATION
    assert decision.reason == OFFER_REASON_RELAXATION_ACCEPTED


def test_a_pending_invitation_owns_the_turn_over_a_new_proposal():
    state = snapshot(
        pending_offer="scale",
        pending_offer_target="PHQ-9",
        round_count=MIN_ROUNDS_FOR_RELAXATION,
    )
    decision = decide(
        text="我们接着聊吧。",
        proposal=relaxation_offer_proposal(),
        state=state,
    )
    # Nothing answered, nothing new proposed: the open question still stands.
    assert decision.action is TurnAction.CHAT
    assert decision.reason == "offer_already_pending"


# ---------------------------------------------------------------------------
# Refusals and cooldowns
# ---------------------------------------------------------------------------


def test_a_recent_refusal_blocks_the_same_invitation():
    state = snapshot(last_scale_decline_round=6, round_count=7)
    decision = decide(proposal=scale_offer_proposal(), state=state)
    assert decision.action is TurnAction.CHAT
    assert decision.reason == "offer_scale_cooldown"


def test_a_refusal_past_the_cooldown_no_longer_blocks():
    state = snapshot(last_scale_decline_round=6, round_count=12)
    decision = decide(proposal=scale_offer_proposal(), state=state)
    assert decision.action is TurnAction.OFFER_SCALE


def test_a_scale_declined_before_is_never_offered_again():
    state = snapshot(declined_scales=("PHQ-9",), round_count=14)
    decision = decide(proposal=scale_offer_proposal("PHQ-9"), state=state)
    assert decision.action is TurnAction.CHAT
    assert decision.reason == "offer_scale_declined_before"


def test_a_relaxation_refusal_blocks_the_relaxation_invitation():
    state = snapshot(
        round_count=MIN_ROUNDS_FOR_RELAXATION,
        last_relaxation_decline_round=MIN_ROUNDS_FOR_RELAXATION - 1,
        relaxation_offered=True,
    )
    decision = decide(proposal=relaxation_offer_proposal(), state=state)
    assert decision.action is TurnAction.CHAT
    assert decision.reason == "offer_relaxation_cooldown"


# ---------------------------------------------------------------------------
# Guards that outrank any activity plan
# ---------------------------------------------------------------------------


def test_a_safety_marker_blocks_a_scale_invitation_from_the_signal():
    decision = decide(proposal=scale_offer_proposal(), risk_marker=True)
    assert decision.action is TurnAction.CHAT
    assert decision.reason == "offer_blocked_risk"


def test_a_safety_marker_blocks_a_relaxation_invitation():
    state = snapshot(round_count=MIN_ROUNDS_FOR_RELAXATION)
    decision = decide(proposal=relaxation_offer_proposal(), state=state, risk_marker=True)
    assert decision.action is TurnAction.CHAT
    assert decision.reason == "offer_blocked_risk"


def test_an_explicit_end_beats_any_invitation():
    decision = decide(
        text="今天就到这儿吧。", proposal=scale_offer_proposal(), explicit_end_requested=True
    )
    assert decision.action is TurnAction.END_SESSION
    assert decision.reason == "explicit_end"


def test_a_running_scale_is_not_interrupted_by_an_invitation():
    state = snapshot(active_scale="PHQ-9", current_item=3, waiting_for_answer=True)
    decision = decide(
        text="3分。",
        proposal=relaxation_offer_proposal(),
        state=state,
        round_count=MIN_ROUNDS_FOR_RELAXATION,
    )
    assert decision.action is TurnAction.CONTINUE_SCALE
    assert decision.scale_name == "PHQ-9"


def test_a_completed_scale_is_not_offered_again():
    state = snapshot(completed_scales=("PHQ-9",))
    decision = decide(proposal=scale_offer_proposal("PHQ-9"), state=state)
    assert decision.action is TurnAction.CHAT
    assert decision.reason == "offer_scale_completed"


def test_a_scale_outside_the_candidate_list_is_refused():
    state = snapshot(scale_candidates=("GAD-7",))
    decision = decide(proposal=scale_offer_proposal("PHQ-9"), state=state)
    assert decision.action is TurnAction.CHAT
    assert decision.reason == "offer_scale_not_a_candidate"


def test_an_invitation_is_not_made_to_someone_who_asked_for_the_floor():
    state = snapshot(round_count=MIN_ROUNDS_FOR_RELAXATION)
    for proposal in (scale_offer_proposal(), relaxation_offer_proposal()):
        decision = decide(
            text="先听我说完。",
            proposal=proposal,
            state=state,
            participant_wants_to_talk=True,
        )
        assert decision.action is TurnAction.CHAT
        assert decision.reason == "offer_blocked_wants_to_talk"


def test_the_wish_to_be_heard_is_detected_from_wording_not_from_mood():
    state = snapshot()
    assert collect_turn_signals("您先听我说完可以吗？", state).participant_wants_to_talk is True
    assert collect_turn_signals("我最近总是很紧张。", state).participant_wants_to_talk is False


# ---------------------------------------------------------------------------
# User initiative
# ---------------------------------------------------------------------------


def test_an_explicit_scale_request_starts_the_top_candidate():
    state = snapshot(scale_candidates=("GAD-7", "PHQ-9"), round_count=6)
    decision = decide(text="我想做个量表看看。", state=state, explicit_scale_requested=True)
    assert decision.action is TurnAction.START_SCALE
    assert decision.scale_name == "GAD-7"
    assert decision.reason == "user_scale_request"


def test_an_explicit_relaxation_request_is_honoured_even_before_the_floor():
    state = snapshot(round_count=1)
    decision = decide(
        text="能不能做个放松训练？",
        state=state,
        explicit_relaxation_requested=True,
    )
    assert decision.action is TurnAction.RECOMMEND_RELAXATION


# ---------------------------------------------------------------------------
# Signals: consent is read from the answer, not from a substring
# ---------------------------------------------------------------------------


def test_consent_is_not_inferred_from_a_sentence_that_merely_contains_agreement():
    state = snapshot(pending_offer="scale", pending_offer_target="PHQ-9")
    texts = [
        "我知道可以放松，但我现在更想把这些说出来。",
        "可以吗？",
        "我做不了。",
        "好，那我们继续聊。",
    ]
    for text in texts:
        signals = collect_turn_signals(text, state)
        assert signals.offer_accepted is False, text


@pytest.mark.parametrize("text", ["可以", "行，那就做一下吧", "好，那就做吧", "试试吧"])
def test_plain_agreement_is_consent(text):
    state = snapshot(pending_offer="scale", pending_offer_target="PHQ-9")
    assert collect_turn_signals(text, state).offer_accepted is True


def test_agreement_is_only_read_while_an_invitation_is_open():
    state = snapshot()
    assert collect_turn_signals("可以", state).offer_accepted is False


# ---------------------------------------------------------------------------
# Signals: a request is a request, a remark is a remark
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "我现在特别烦，能不能做个放松训练？",
        "我想做个放松训练",
        "可以先做个呼吸练习吗",
    ],
)
def test_relaxation_requests_are_detected(text):
    from conversation.turn_signals import detect_explicit_relaxation_request

    assert detect_explicit_relaxation_request(text)[0] is True


@pytest.mark.parametrize(
    "text",
    [
        "我知道可以放松，但我现在更想把这些说出来。",
        "听说放松有用，但我不想现在做。",
        "以前做过放松训练，对我没用。",
        "别做放松了。",
    ],
)
def test_remarks_about_relaxation_are_not_requests(text):
    from conversation.turn_signals import detect_explicit_relaxation_request

    assert detect_explicit_relaxation_request(text)[0] is False, text


def test_the_word_special_does_not_cancel_a_request():
    # Regression: a bare "别" in the negation list matched inside "特别" and
    # silently cancelled every request that followed it.
    from conversation.turn_signals import detect_explicit_relaxation_request

    assert detect_explicit_relaxation_request("我最近特别累，想做个放松训练")[0] is True
    assert detect_explicit_relaxation_request("我最近特别累，不想做放松训练")[0] is False
