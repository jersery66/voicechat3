"""Session-scoped ledger of scale and relaxation invitations.

The decision layer may *suggest* an invitation; nothing else in the system
remembers that one was made.  Without that memory three things go wrong, all
of them observed in practice:

- the same questionnaire is proposed every turn;
- a refusal is forgotten, so the participant is asked again two turns later;
- an invitation is still "open" while the interview has already moved on.

This ledger is the single owner of that memory.  It is deliberately plain
Python (no model, no I/O) and it is the only object in the offer path that
mutates, which keeps ``TurnPolicy`` pure.

Lifecycle::

    (none) --offer--> PENDING --ask details--> CLARIFYING --answer--> PENDING
                         |                        |
                         |--accept--> ACCEPTED <--|
                         |--decline-> DECLINED
                         |--topic drift----------> EXPIRED   (needs evidence)
                         |--session end / risk---> CANCELLED
                         |--newer request---------> CANCELLED (superseded)

Two rules are load-bearing and must not be relaxed:

1. **No answer timeout.**  A turn count alone never proves the participant
   lost interest; people answer two or three turns later all the time.  The
   only path to ``EXPIRED`` runs through :meth:`OfferLedger.expire_on_topic_drift`,
   which requires the conversation to have moved on for several consecutive
   turns *and* the participant to have stopped referring to the invitation.
2. **A closed invitation cannot be answered.**  Once a record is terminal,
   an ambiguous "好" in a later turn must not execute anything.  Consent has
   to arrive while the invitation is open, or as a fresh explicit request.
"""

from __future__ import annotations

from typing import Optional

from decision.timing import (
    DEFAULT_OFFER_POLICY,
    OfferKind,
    OfferPolicy,
    OfferRecord,
    OfferState,
    OfferStatus,
)


def offer_snapshot_fields(state: OfferState) -> dict[str, object]:
    """Flatten an :class:`OfferState` into ``TurnStateSnapshot`` keyword fields.

    Pure function so callers that only hold a snapshot (tests, adapters, the
    trajectory harness) can build the same fields the pipeline builds.
    """
    pending = state.pending
    return {
        "scale_candidates": (),  # filled by the caller from the registry
        "pending_offer": pending.kind.value if pending is not None else None,
        "pending_offer_target": pending.target if pending is not None else None,
        "pending_offer_round": pending.made_at_round if pending is not None else None,
        "pending_offer_status": state.pending_status,
        "offered_scales": state.offered_scales(),
        "declined_scales": state.declined_scales(),
        "last_scale_decline_round": state.declined_round(OfferKind.SCALE),
        "relaxation_offered": state.relaxation_offered(),
        "relaxation_declined": state.relaxation_declined(),
        "last_relaxation_decline_round": state.declined_round(OfferKind.RELAXATION),
    }


class OfferLedger:
    """Mutable record of every invitation in one session."""

    def __init__(self, policy: OfferPolicy | None = None) -> None:
        self.policy = policy or DEFAULT_OFFER_POLICY
        self._records: list[OfferRecord] = []
        #: First round of the current run of "conversation moved on" turns.
        self._drift_since: Optional[int] = None

    # -- lifecycle ---------------------------------------------------------

    def reset(self) -> None:
        self._records.clear()
        self._drift_since = None

    # -- reads -------------------------------------------------------------

    def state(self) -> OfferState:
        return OfferState(records=tuple(self._records))

    def pending(self) -> Optional[OfferRecord]:
        return self.state().pending

    def has_pending(self) -> bool:
        return self.pending() is not None

    def record(self, offer_id: str) -> Optional[OfferRecord]:
        for record in self._records:
            if record.offer_id == offer_id:
                return record
        return None

    # -- writes ------------------------------------------------------------

    def _next_id(self, kind: OfferKind, round_count: int) -> str:
        return f"{kind.value}-{round_count}-{len(self._records) + 1}"

    def record_offer(
        self,
        kind: OfferKind,
        *,
        target: Optional[str],
        round_count: int,
        session_state: Optional[str] = None,
        source: str = "policy",
    ) -> OfferRecord:
        """Open a new invitation, cancelling any unclosed one.

        Two open invitations would make "yes" ambiguous, so the older one is
        cancelled with an explicit note rather than left dangling.  If the
        participant themselves asked, ``source="explicit_request"`` keeps that
        fact auditable.
        """
        current_round = max(0, int(round_count))
        for index, record in enumerate(self._records):
            if record.status.is_open:
                self._records[index] = record.resolved(
                    OfferStatus.CANCELLED,
                    current_round,
                    note="superseded_by_new_offer",
                )
        record = OfferRecord(
            offer_id=self._next_id(OfferKind(kind), current_round),
            kind=OfferKind(kind),
            target=target,
            made_at_round=current_round,
            last_touched_round=current_round,
            status=OfferStatus.PENDING,
            session_state=session_state,
            source=source,
        )
        self._records.append(record)
        self._drift_since = None
        return record

    def touch_pending(self, round_count: int) -> Optional[OfferRecord]:
        """Mark the open invitation as still relevant this turn."""
        current_round = max(0, int(round_count))
        for index in range(len(self._records) - 1, -1, -1):
            record = self._records[index]
            if record.status.is_open:
                updated = record.touched(current_round)
                self._records[index] = updated
                self._drift_since = None
                return updated
        return None

    def mark_clarifying(self, round_count: int) -> Optional[OfferRecord]:
        """The participant is asking about the invitation, not answering it."""
        current_round = max(0, int(round_count))
        for index in range(len(self._records) - 1, -1, -1):
            record = self._records[index]
            if record.status.is_open:
                updated = record.clarifying(current_round)
                self._records[index] = updated
                self._drift_since = None
                return updated
        return None

    def resolve(
        self,
        offer_id: str,
        status: OfferStatus,
        round_count: int,
        *,
        note: Optional[str] = None,
    ) -> Optional[OfferRecord]:
        """Close one specific invitation."""
        if OfferStatus(status).is_open:
            raise ValueError("resolve() needs a terminal status")
        current_round = max(0, int(round_count))
        for index, record in enumerate(self._records):
            if record.offer_id == offer_id:
                if not record.status.is_open:
                    return None
                resolved = record.resolved(status, current_round, note=note)
                self._records[index] = resolved
                return resolved
        return None

    def resolve_pending(
        self,
        status: OfferStatus,
        round_count: int,
        *,
        note: Optional[str] = None,
    ) -> Optional[OfferRecord]:
        """Close the open invitation, if any, with ``status``."""
        pending = self.pending()
        if pending is None:
            return None
        return self.resolve(pending.offer_id, status, round_count, note=note)

    def cancel_all(self, round_count: int, reason: str) -> list[OfferRecord]:
        """Cancel every open invitation (session end, safety, hard reset)."""
        current_round = max(0, int(round_count))
        cancelled: list[OfferRecord] = []
        for index, record in enumerate(self._records):
            if not record.status.is_open:
                continue
            resolved = record.resolved(
                OfferStatus.CANCELLED, current_round, note=reason
            )
            self._records[index] = resolved
            cancelled.append(resolved)
        self._drift_since = None
        return cancelled

    def expire_on_topic_drift(self, round_count: int, *, engaged: bool) -> list[OfferRecord]:
        """Expire an invitation only once the conversation has really moved on.

        ``engaged`` is the caller's judgement that this turn still concerns the
        invitation (an answer, a question about it, or a reference to it).  Any
        engaged turn resets the drift counter, which is what lets a participant
        come back three turns later and still consent.
        """
        current_round = max(0, int(round_count))
        pending = self.pending()
        if pending is None:
            self._drift_since = None
            return []
        if engaged:
            self._drift_since = None
            return []
        if self._drift_since is None:
            self._drift_since = current_round
        # The drift run is inclusive of the round it started in.
        elapsed = current_round - self._drift_since + 1
        if elapsed < self.policy.topic_drift_grace_rounds:
            return []
        resolved = pending.resolved(
            OfferStatus.EXPIRED, current_round, note="topic_drift"
        )
        for index, record in enumerate(self._records):
            if record.offer_id == pending.offer_id:
                self._records[index] = resolved
                break
        self._drift_since = None
        return [resolved]

    # -- helpers for the policy snapshot -----------------------------------

    def snapshot_fields(self, round_count: int) -> dict[str, object]:
        """Flatten the ledger into :class:`TurnStateSnapshot` keyword fields."""
        del round_count  # expiry is applied by :meth:`expire_on_topic_drift`
        return offer_snapshot_fields(self.state())
