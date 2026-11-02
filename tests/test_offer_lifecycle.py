"""Invitation lifecycle: consent that arrives late, and consent that never did.

The previous ledger closed an unanswered invitation after two turns.  Real
interviews do not work that way -- participants ask what a questionnaire
involves, think about it, and answer two or three turns later.  Treating the
turn count as an answer threw away consent that was still coming.

These tests pin the replacement contract:

* an open invitation survives turns and questions, and expires only after the
  conversation has visibly moved on;
* a closed invitation can never be answered by a later "好";
* postponement is not refusal, and a double negative is not a refusal either;
* nothing executable happens without an accepted invitation or an explicit
  request.
"""

from __future__ import annotations

import pytest

from config import MIN_ROUNDS_BEFORE_SCALE
from conversation.contracts import (
    RouterProposal,
    TurnAction,
    TurnStateSnapshot,
)
from conversation.offer_ledger import OfferLedger, offer_snapshot_fields
from conversation.turn_policy import TurnPolicy
from conversation.turn_signals import (
    collect_turn_signals,
    detect_offer_answer,
    detect_offer_deferral,
    detect_offer_question,
)
from decision.timing import (
    OFFER_REASON_CLARIFYING,
    OFFER_REASON_DEFERRED,
    OFFER_REASON_SCALE_ACCEPTED,
    OFFER_REASON_SCALE_DECLINED,
    OfferKind,
    OfferPolicy,
    OfferStatus,
)


class Session:
    """A minimal session that mirrors the pipeline's invitation bookkeeping."""

    def __init__(self, policy: OfferPolicy | None = None, **policy_overrides) -> None:
        if policy_overrides:
            policy = OfferPolicy(**policy_overrides)
        self.ledger = OfferLedger(policy)
        self.round = 0

    def snapshot(self, **overrides) -> TurnStateSnapshot:
        # Offer bookkeeping first, then the world state.  ``offer_snapshot_fields``
        # deliberately leaves ``scale_candidates`` empty (the registry owns it),
        # so writing it afterwards is what makes the snapshot realistic.
        values = dict(offer_snapshot_fields(self.ledger.state()))
        values.update(
            {
                "session_state": "CHATTING",
                "round_count": self.round,
                "active_scale": None,
                "current_item": None,
                "waiting_for_answer": False,
                "completed_scales": (),
                "relaxation_used": False,
                "game_active": False,
                "time_limit_reached": False,
                "scale_candidates": ("PHQ-9", "GAD-7"),
                "risk_marker": False,
            }
        )
        values.update(overrides)
        return TurnStateSnapshot(**values)

    def offer(self, kind=OfferKind.SCALE, target="GAD-7", at=1) -> None:
        self.round = at
        self.ledger.record_offer(kind, target=target, round_count=at)

    def turn(self, text: str, *, at: int | None = None, proposal=None):
        """Run one turn and apply the policy decision like the pipeline does."""
        if at is not None:
            self.round = at
            # Drift is the only expiry path, exactly as in the pipeline.
            self.ledger.expire_on_topic_drift(
                self.round,
                engaged=detect_offer_question(text) or any(
                    marker in text for marker in ("量表", "问卷", "放松", "刚才", "那个")
                ),
            )
        snapshot = self.snapshot(round_count=self.round)
        signals = collect_turn_signals(text, snapshot)
        decision = TurnPolicy().decide(
            user_text=text,
            proposal=proposal or RouterProposal.fallback("test"),
            snapshot=snapshot,
            signals=signals,
        )

        if decision.reason == OFFER_REASON_SCALE_ACCEPTED:
            self.ledger.resolve_pending(OfferStatus.ACCEPTED, self.round)
        elif decision.reason == OFFER_REASON_SCALE_DECLINED:
            self.ledger.resolve_pending(OfferStatus.DECLINED, self.round)
        elif decision.reason == OFFER_REASON_CLARIFYING:
            self.ledger.mark_clarifying(self.round)
        elif decision.reason == OFFER_REASON_DEFERRED:
            self.ledger.touch_pending(self.round)
        elif decision.action is TurnAction.END_SESSION:
            self.ledger.cancel_all(self.round, "session_end")
        elif signals.risk_marker:
            self.ledger.cancel_all(self.round, "risk")
        return decision, snapshot, signals

    def status(self) -> str:
        return self.ledger.state().pending_status


# ---------------------------------------------------------------------------
# The lifecycle itself
# ---------------------------------------------------------------------------


def test_the_lifecycle_has_an_explicit_state_for_every_outcome():
    assert {status.value for status in OfferStatus} == {
        "none",
        "pending",
        "clarifying",
        "accepted",
        "declined",
        "expired",
        "cancelled",
    }


def test_every_invitation_records_who_what_when_and_why():
    ledger = OfferLedger()
    record = ledger.record_offer(
        OfferKind.SCALE,
        target="GAD-7",
        round_count=5,
        session_state="CHATTING",
    )
    assert record.offer_id
    assert record.kind is OfferKind.SCALE
    assert record.target == "GAD-7"
    assert record.made_at_round == 5
    assert record.last_touched_round == 5
    assert record.status is OfferStatus.PENDING
    assert record.session_state == "CHATTING"
    assert record.source == "policy"

    closed = ledger.resolve_pending(OfferStatus.DECLINED, 7, note="refusal")
    assert closed is not None
    assert closed.resolved_at_round == 7
    assert closed.resolution_note == "refusal"


def test_asking_what_the_invitation_involves_keeps_it_open():
    session = Session()
    session.offer(target="GAD-7", at=5)
    decision, _, signals = session.turn("可以先说一下是什么吗？", at=6)

    assert signals.offer_question is True
    assert decision.action is TurnAction.CHAT
    assert decision.reason == OFFER_REASON_CLARIFYING
    assert session.status() == "clarifying"
    assert session.ledger.pending() is not None


def test_consent_two_turns_later_still_starts_the_scale():
    """The case the old two-round timeout silently swallowed."""
    session = Session()
    session.offer(target="GAD-7", at=5)
    session.turn("可以先说一下是什么吗？", at=6)
    session.turn("嗯，我再想一下。", at=7)
    decision, snap, _ = session.turn("好，那就做吧。", at=8)

    assert decision.action is TurnAction.START_SCALE
    assert decision.scale_name == "GAD-7"
    assert decision.reason == OFFER_REASON_SCALE_ACCEPTED
    assert snap.pending_offer_target == "GAD-7"
    assert session.status() == "none"


def test_a_late_yes_after_the_context_is_gone_does_not_start_anything():
    session = Session(topic_drift_grace_rounds=3)
    session.offer(target="GAD-7", at=5)
    # Three turns that neither answer nor mention the invitation: the topic has
    # genuinely moved on.
    for round_number in (6, 7, 8):
        session.turn("我最近在想要不要换个工作。", at=round_number)
    assert session.status() == "none"

    decision, _, _ = session.turn("好。", at=9)
    assert decision.action is not TurnAction.START_SCALE
    assert session.ledger.has_pending() is False


def test_referring_to_the_invitation_resets_the_drift_clock():
    session = Session(topic_drift_grace_rounds=3)
    session.offer(target="GAD-7", at=5)
    session.turn("工作上的事挺多的。", at=6)
    session.turn("你说的那个量表要多久？", at=7)
    assert session.status() in {"pending", "clarifying"}
    session.turn("这两天睡得很差。", at=8)
    session.turn("白天也没精神。", at=9)
    assert session.ledger.has_pending() is True
    assert session.status() in {"pending", "clarifying"}


def test_postponement_keeps_the_invitation_and_is_not_a_refusal():
    session = Session()
    session.offer(target="GAD-7", at=5)
    decision, _, signals = session.turn("现在不要，待会再说。", at=6)

    assert signals.offer_declined is False
    assert signals.offer_deferred is True
    assert decision.reason == OFFER_REASON_DEFERRED
    assert session.ledger.has_pending() is True


def test_a_refusal_closes_the_invitation_and_starts_nothing():
    session = Session()
    session.offer(target="GAD-7", at=5)
    decision, _, _ = session.turn("不用了，我现在不想做。", at=6)

    assert decision.reason == OFFER_REASON_SCALE_DECLINED
    assert decision.action is TurnAction.CHAT
    assert session.ledger.state().declined_scales() == ("GAD-7",)


def test_a_vague_answer_is_clarified_not_assumed():
    session = Session()
    session.offer(target="GAD-7", at=5)
    decision, _, signals = session.turn("嗯……", at=6)

    assert signals.offer_answer_ambiguous is True
    assert decision.action is TurnAction.CLARIFY_INPUT
    assert session.ledger.has_pending() is True


def test_session_end_cancels_every_open_invitation():
    session = Session()
    session.offer(target="GAD-7", at=5)
    decision, _, _ = session.turn("结束吧", at=6)

    assert decision.action is TurnAction.END_SESSION
    record = session.ledger.state().records[0]
    assert record.status is OfferStatus.CANCELLED
    assert record.resolution_note == "session_end"


def test_a_crisis_turn_cancels_the_invitation_and_refuses_to_act():
    session = Session()
    session.offer(target="GAD-7", at=5)
    decision, _, signals = session.turn("我不想活了。", at=6)

    assert signals.risk_marker is True
    assert decision.action is TurnAction.CHAT
    assert decision.reason == "offer_blocked_risk"
    record = session.ledger.state().records[0]
    assert record.status is OfferStatus.CANCELLED
    assert record.resolution_note == "risk"


def test_a_newer_invitation_cancels_the_older_one():
    session = Session()
    session.offer(kind=OfferKind.SCALE, target="GAD-7", at=5)
    session.offer(kind=OfferKind.RELAXATION, target=None, at=6)

    open_records = [
        record for record in session.ledger.state().records if record.status.is_open
    ]
    assert len(open_records) == 1
    assert open_records[0].kind is OfferKind.RELAXATION
    cancelled = [
        record
        for record in session.ledger.state().records
        if record.status is OfferStatus.CANCELLED
    ]
    assert cancelled[0].resolution_note == "superseded_by_new_offer"


# ---------------------------------------------------------------------------
# Recognition stays conservative
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "answer", "question", "deferred"),
    [
        ("好，那就做吧。", "accept", False, False),
        ("可以先说一下是什么吗？", "defer", True, False),
        ("我知道可以放松，但我想继续聊。", "none", False, False),
        ("不是不想做，我想等一下。", "defer", False, True),
        ("先不做量表。", "decline", False, False),
        ("我愿意做刚才那个。", "accept", False, False),
        ("哪个量表？", "defer", True, False),
        ("算了。", "decline", False, False),
        ("现在不要，待会再说。", "defer", False, True),
        ("我现在特别烦，能做个放松训练吗？", "defer", True, False),
    ],
)
def test_the_ten_named_phrases_are_classified_conservatively(
    text, answer, question, deferred
):
    assert detect_offer_answer(text) == answer
    assert detect_offer_question(text) is question
    assert detect_offer_deferral(text) is deferred


def test_agreement_is_only_read_while_something_is_actually_open():
    session = Session()
    decision, _, signals = session.turn("好，那就做吧。", at=6)
    assert signals.offer_accepted is False
    assert decision.action is not TurnAction.START_SCALE


def test_the_floor_does_not_block_a_participant_who_asks_for_the_scale():
    session = Session()
    session.round = 2
    snapshot = session.snapshot()
    assert snapshot.round_count < MIN_ROUNDS_BEFORE_SCALE
    decision, _, _ = session.turn("我想做个量表看看", at=2)
    assert decision.action is TurnAction.START_SCALE
    assert decision.reason == "user_scale_request"
