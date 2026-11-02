"""Intervention-timing vocabulary for the Jev decision layer.

The decision layer used to answer "which action label fits this utterance".
That is the wrong question.  What the interview actually needs is:

    keep talking / offer an assessment / offer a relaxation exercise / clarify

Four answers, decided from the *history* of the interview rather than from one
sentence.  This module owns that vocabulary and the offer bookkeeping that
makes the four answers safe to act on.

Three separations are load-bearing and must not collapse:

1. **An offer is not an execution.**  ``OFFER_SCALE`` means "ask the
   participant whether they are willing to do a questionnaire".  It never
   starts one.  ``OFFER_RELAXATION`` is an invitation, never a session.
2. **A suggestion is not a decision.**  Nothing here authorises anything;
   ``TurnPolicy`` still approves every executable step.
3. **An offer must name a registered scale.**  If no candidate exists, there
   is nothing to offer -- the honest answer is to keep interviewing, not to
   emit ``start_scale`` with an empty target.

The offer state is also the only place that remembers refusals, which is what
stops the system from asking the same question every other turn.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# The four answers
# ---------------------------------------------------------------------------


class InterventionIntent(str, Enum):
    """What the interview should do next.  Internal decision semantics."""

    CONTINUE_INTERVIEW = "continue_interview"
    OFFER_SCALE = "offer_scale"
    OFFER_RELAXATION = "offer_relaxation"
    CLARIFY_FIRST = "clarify_first"


INTERVENTION_LABELS: tuple[str, ...] = tuple(intent.value for intent in InterventionIntent)

#: Option text shown to the decision model, in presentation order.  The model
#: reads a position letter, so the text matters far less than the letter does;
#: it is kept explicit so a human reading a transcript can follow the decision.
INTERVENTION_OPTIONS: tuple[tuple[str, InterventionIntent], ...] = (
    ("Continue interviewing", InterventionIntent.CONTINUE_INTERVIEW),
    ("Offer a scale assessment", InterventionIntent.OFFER_SCALE),
    ("Offer a relaxation exercise", InterventionIntent.OFFER_RELAXATION),
    ("Clarify before proceeding", InterventionIntent.CLARIFY_FIRST),
)

INTERVENTION_OPTION_TEXTS: tuple[str, ...] = tuple(
    text for text, _intent in INTERVENTION_OPTIONS
)

#: English template.  Kept as the default because the official Tacit checkpoint
#: is English-trained and reads this prompt far better than a Chinese one; the
#: Chinese variant exists so the difference can be measured, not assumed.
INTERVENTION_QUESTION_EN = (
    "Given the interview phase so far, the reported symptoms, the assessment "
    "history, the offers already made, and the participant's stated wishes, "
    "what is the most appropriate next interaction?"
)

INTERVENTION_QUESTION_ZH = (
    "结合目前的访谈阶段、已了解的症状信息、量表与放松训练的历史、既往建议"
    "以及来访者表达的意愿，这一轮最合适的下一步交互是什么？"
)

_TEMPLATE_BY_NAME = {"en": INTERVENTION_QUESTION_EN, "zh": INTERVENTION_QUESTION_ZH}

# ---------------------------------------------------------------------------
# Interview phase (derived, never inferred by a model)
# ---------------------------------------------------------------------------


class InterviewPhase(str, Enum):
    OPENING = "opening"
    EXPLORATION = "exploration"
    ASSESSMENT = "assessment"
    CLOSURE = "closure"


_PHASE_TEXT_ZH = {
    InterviewPhase.OPENING: "建立关系",
    InterviewPhase.EXPLORATION: "症状探索",
    InterviewPhase.ASSESSMENT: "量表或训练",
    InterviewPhase.CLOSURE: "结束阶段",
}

_ENDING_STATES = frozenset({"SESSION_ENDING", "SESSION_ENDED"})


def interview_phase(
    *,
    round_count: int,
    session_state: str = "CHATTING",
    active_scale: Optional[str] = None,
    offers: Optional["OfferState"] = None,
    relaxation_active: bool = False,
    opening_rounds: int = 2,
) -> InterviewPhase:
    """Project verifiable runtime facts onto one coarse interview phase.

    This is a *label for the model's state block*, not a decision.  It uses
    only counters the pipeline already owns, so the model cannot invent a phase
    the session is not actually in.
    """
    if str(session_state) in _ENDING_STATES:
        return InterviewPhase.CLOSURE
    if active_scale or relaxation_active:
        return InterviewPhase.ASSESSMENT
    if offers is not None and offers.pending is not None:
        return InterviewPhase.ASSESSMENT
    if int(round_count) <= int(opening_rounds):
        return InterviewPhase.OPENING
    return InterviewPhase.EXPLORATION


def phase_text(phase: InterviewPhase) -> str:
    return _PHASE_TEXT_ZH.get(phase, str(phase))


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------


class OfferKind(str, Enum):
    SCALE = "scale"
    RELAXATION = "relaxation"


class OfferStatus(str, Enum):
    """Lifecycle of one invitation.

    ``NONE`` is a *view* over a session with nothing open; it is never written
    to a record.  ``CLARIFYING`` exists because "what would that involve?"
    is neither consent nor refusal, and treating it as either is how an
    interview ends up starting a questionnaire nobody agreed to.

    Nothing here expires by turn count alone.  ``EXPIRED`` requires evidence
    that the conversation has actually moved on (see
    ``OfferPolicy.topic_drift_grace_rounds``); ``CANCELLED`` records an
    explicit cause such as a session end, a safety escalation, or a newer
    request replacing this one.
    """

    NONE = "none"
    PENDING = "pending"
    CLARIFYING = "clarifying"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

    @property
    def is_open(self) -> bool:
        return self in (OfferStatus.PENDING, OfferStatus.CLARIFYING)


#: Historical name kept so existing call sites read naturally.  ``SUPERSEDED``
#: is gone: a replaced invitation is simply cancelled with a note.
OfferOutcome = OfferStatus


class OfferRecord(BaseModel):
    """One invitation the assistant actually made."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    offer_id: str = ""
    kind: OfferKind
    target: Optional[str] = None
    made_at_round: int = Field(default=0, ge=0)
    last_touched_round: int = Field(default=0, ge=0)
    resolved_at_round: Optional[int] = Field(default=None, ge=0)
    status: OfferStatus = OfferStatus.PENDING
    #: Session state at the moment the invitation was made ("CHATTING", ...).
    session_state: Optional[str] = None
    #: Why the invitation was closed: "session_end", "risk", "superseded", ...
    resolution_note: Optional[str] = None
    #: "policy" when the system proposed it, "explicit_request" when the
    #: participant asked.  The distinction matters when auditing consent.
    source: str = "policy"

    @property
    def is_open(self) -> bool:
        return self.status.is_open

    def resolved(
        self,
        status: OfferStatus,
        round_count: int,
        *,
        note: Optional[str] = None,
    ) -> "OfferRecord":
        return self.model_copy(
            update={
                "status": OfferStatus(status),
                "resolved_at_round": max(0, int(round_count)),
                "last_touched_round": max(0, int(round_count)),
                "resolution_note": note,
            }
        )

    def touched(self, round_count: int) -> "OfferRecord":
        return self.model_copy(
            update={"last_touched_round": max(0, int(round_count))}
        )

    def clarifying(self, round_count: int) -> "OfferRecord":
        return self.model_copy(
            update={
                "status": OfferStatus.CLARIFYING,
                "last_touched_round": max(0, int(round_count)),
            }
        )

    def redacted(self) -> dict[str, Any]:
        return {
            "offer_id": self.offer_id,
            "kind": self.kind.value,
            "target": self.target,
            "made_at_round": self.made_at_round,
            "last_touched_round": self.last_touched_round,
            "status": self.status.value,
            "resolved_at_round": self.resolved_at_round,
            "session_state": self.session_state,
            "resolution_note": self.resolution_note,
            "source": self.source,
        }


class OfferState(BaseModel):
    """Immutable view of every invitation in one session.

    Read by the decision layer (as state) and by ``TurnPolicy`` (as gates).
    The mutable ledger that produces it lives in ``conversation.offer_ledger``.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    records: tuple[OfferRecord, ...] = ()

    # -- queries -----------------------------------------------------------

    @property
    def pending(self) -> Optional[OfferRecord]:
        """The open invitation, whether it is unanswered or being discussed."""
        for record in reversed(self.records):
            if record.status.is_open:
                return record
        return None

    @property
    def pending_status(self) -> str:
        pending = self.pending
        return pending.status.value if pending is not None else OfferStatus.NONE.value

    @property
    def clarifying(self) -> bool:
        pending = self.pending
        return pending is not None and pending.status is OfferStatus.CLARIFYING

    def pending_of(self, kind: OfferKind) -> Optional[OfferRecord]:
        pending = self.pending
        return pending if pending is not None and pending.kind is kind else None

    def open_offers(self) -> tuple[OfferRecord, ...]:
        return tuple(record for record in self.records if record.status.is_open)

    def offered_scales(self) -> tuple[str, ...]:
        return tuple(
            record.target
            for record in self.records
            if record.kind is OfferKind.SCALE and record.target
        )

    def declined_scales(self) -> tuple[str, ...]:
        return tuple(
            record.target
            for record in self.records
            if record.kind is OfferKind.SCALE
            and record.status is OfferStatus.DECLINED
            and record.target
        )

    def accepted_scales(self) -> tuple[str, ...]:
        return tuple(
            record.target
            for record in self.records
            if record.kind is OfferKind.SCALE
            and record.status is OfferStatus.ACCEPTED
            and record.target
        )

    def relaxation_offered(self) -> bool:
        return any(record.kind is OfferKind.RELAXATION for record in self.records)

    def relaxation_declined(self) -> bool:
        return any(
            record.kind is OfferKind.RELAXATION and record.status is OfferStatus.DECLINED
            for record in self.records
        )

    def relaxation_accepted(self) -> bool:
        return any(
            record.kind is OfferKind.RELAXATION and record.status is OfferStatus.ACCEPTED
            for record in self.records
        )

    def last_resolution_round(self, kind: OfferKind) -> Optional[int]:
        rounds = [
            record.resolved_at_round
            for record in self.records
            if record.kind is kind and record.resolved_at_round is not None
        ]
        return max(rounds) if rounds else None

    def declined_round(self, kind: OfferKind, target: Optional[str] = None) -> Optional[int]:
        rounds = [
            record.resolved_at_round
            for record in self.records
            if record.kind is kind
            and record.status is OfferStatus.DECLINED
            and record.resolved_at_round is not None
            and (target is None or record.target == target)
        ]
        return max(rounds) if rounds else None

    def cooldown_active(self, kind: OfferKind, round_count: int, cooldown: int) -> bool:
        """True while a refusal still suppresses the same kind of offer."""
        if cooldown <= 0:
            return False
        declined_at = self.declined_round(kind)
        if declined_at is None:
            return False
        return (int(round_count) - declined_at) < int(cooldown)

    def total_offers(self, kind: Optional[OfferKind] = None) -> int:
        if kind is None:
            return len(self.records)
        return sum(1 for record in self.records if record.kind is kind)

    # -- serialisation -----------------------------------------------------

    def summary_parts(self) -> list[str]:
        """Chinese state lines for the model's state block."""
        parts: list[str] = []
        scales = self.offered_scales()
        if scales:
            parts.append("已建议过的量表：" + "、".join(dict.fromkeys(scales)))
        declined = self.declined_scales()
        if declined:
            parts.append("来访者拒绝过的量表：" + "、".join(dict.fromkeys(declined)))
        pending = self.pending
        if pending is not None:
            label = "量表" if pending.kind is OfferKind.SCALE else "放松训练"
            target = f"（{pending.target}）" if pending.target else ""
            if pending.status is OfferStatus.CLARIFYING:
                parts.append(
                    f"已发出{label}{target}邀请，来访者正在询问细节；邀请仍然有效，不要重复发出"
                )
            else:
                parts.append(f"已发出{label}{target}邀请，尚未得到明确答复")
        if self.relaxation_offered():
            parts.append("已邀请过放松训练")
        if self.relaxation_declined():
            parts.append("来访者拒绝过放松训练")
        if not parts:
            parts.append("尚未提出过量表或放松训练建议")
        return parts

    def redacted(self) -> dict[str, Any]:
        """Metadata-only projection for logs and the decision journal."""
        return {
            "records": [record.redacted() for record in self.records],
            "pending": self.pending.kind.value if self.pending else None,
            "pending_status": self.pending_status,
        }


class OfferPolicy(BaseModel):
    """Numeric gates for offering.  Floors, not targets.

    ``MIN_ROUNDS_BEFORE_SCALE`` / ``MIN_ROUNDS_FOR_RELAXATION`` in ``config``
    remain the hard programme floors.  The model decides *whether this is a
    good moment*; these numbers only stop it from being obviously wrong.

    ``topic_drift_grace_rounds`` is deliberately *not* an answer timeout: an
    unanswered invitation stays valid across turns, because participants
    commonly answer two or three turns later.  It only expires after the
    conversation has visibly moved on for this many consecutive turns without
    any reference to the invitation (see ``OfferLedger.expire_on_topic_drift``).
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    scale_cooldown_rounds: int = Field(default=4, ge=0)
    relaxation_cooldown_rounds: int = Field(default=4, ge=0)
    topic_drift_grace_rounds: int = Field(default=3, ge=1)
    block_scale_after_decline: bool = True
    block_relaxation_after_decline: bool = True


DEFAULT_OFFER_POLICY = OfferPolicy()


#: Stable decision reasons.  ``TurnPolicy`` emits them and the session ledger
#: reads them back, so they are shared vocabulary rather than private strings.
OFFER_REASON_SCALE_ACCEPTED = "offer_scale_accepted"
OFFER_REASON_SCALE_DECLINED = "offer_scale_declined"
OFFER_REASON_RELAXATION_ACCEPTED = "offer_relaxation_accepted"
OFFER_REASON_RELAXATION_DECLINED = "offer_relaxation_declined"
OFFER_REASON_ANSWER_AMBIGUOUS = "offer_answer_ambiguous"
OFFER_REASON_CLARIFYING = "offer_clarifying"
OFFER_REASON_DEFERRED = "offer_deferred"
OFFER_REASON_TARGET_MISSING = "offer_scale_target_missing"


#: Words that keep an invitation "in view" even without an answer.  Used to
#: decide topic drift: answering "那个量表要多久" is engagement, not drift.
_OFFER_REFERENCE_MARKERS = (
    "量表", "问卷", "测试", "测一下", "测测", "题目", "评分",
    "放松", "呼吸", "训练", "冥想",
    "刚才", "刚刚", "那个", "这个", "你说的", "你提的",
)


def mentions_offer(text: str) -> bool:
    """True when the utterance still refers to an invitation under discussion."""
    normalized = (text or "").strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in _OFFER_REFERENCE_MARKERS)

#: reason -> ledger outcome, for the two reasons that close an invitation.
OUTCOME_BY_REASON = {
    OFFER_REASON_SCALE_ACCEPTED: OfferOutcome.ACCEPTED,
    OFFER_REASON_RELAXATION_ACCEPTED: OfferOutcome.ACCEPTED,
    OFFER_REASON_SCALE_DECLINED: OfferOutcome.DECLINED,
    OFFER_REASON_RELAXATION_DECLINED: OfferOutcome.DECLINED,
    # The invitation stays open; the ledger distinguishes "explaining" from
    # "postponed" so the state block can say which one the model is in.
    OFFER_REASON_CLARIFYING: OfferOutcome.CLARIFYING,
    OFFER_REASON_DEFERRED: OfferOutcome.PENDING,
}


# ---------------------------------------------------------------------------
# Scale candidates
# ---------------------------------------------------------------------------


class ScaleCandidate(BaseModel):
    """One scale the system is allowed to offer right now."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str
    reason: str = "registered"
    priority: int = 0

    def redacted(self) -> dict[str, Any]:
        return {"name": self.name, "reason": self.reason, "priority": self.priority}


def rank_scale_candidates(
    *,
    registered: Sequence[str],
    completed: Sequence[str] = (),
    administered: Sequence[str] = (),
    declined: Sequence[str] = (),
    declined_blocks: bool = True,
) -> tuple[ScaleCandidate, ...]:
    """Order the scales that may still be offered.

    ``registered`` is the ScaleRuntime registry, so no name can appear here
    that the runtime could not actually start.  Incomplete-but-administered
    scales come first: resuming one is almost always more appropriate than
    opening a new instrument.
    """
    done = {str(name) for name in completed}
    blocked = {str(name) for name in declined} if declined_blocks else set()
    seen: set[str] = set()
    resume: list[ScaleCandidate] = []
    fresh: list[ScaleCandidate] = []

    administered_set = {str(name) for name in administered}
    for index, raw_name in enumerate(registered):
        name = str(raw_name)
        if name in seen or name in done or name in blocked:
            continue
        seen.add(name)
        if name in administered_set:
            resume.append(ScaleCandidate(name=name, reason="incomplete", priority=index))
        else:
            fresh.append(ScaleCandidate(name=name, reason="registered", priority=index))

    resume.sort(key=lambda candidate: candidate.priority)
    fresh.sort(key=lambda candidate: candidate.priority)
    return tuple(resume + fresh)


def scale_choice_option_texts(candidates: Sequence[ScaleCandidate]) -> tuple[str, ...]:
    """Options for the conditional "which scale" question.

    The trailing "none" option is not decoration: without it the model is
    forced to name a scale even when the better answer is to hold off, which is
    exactly the failure mode that produced ``start_scale`` with no target.
    """
    return tuple(candidate.name for candidate in candidates) + (SCALE_CHOICE_NONE,)


SCALE_CHOICE_NONE = "No scale for now"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def normalize_intervention(value: Any) -> Optional[str]:
    """Accept either the label or the option text and return the label."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower().replace("-", "_").replace(" ", "_")
    if lowered in INTERVENTION_LABELS:
        return lowered
    for option_text, intent in INTERVENTION_OPTIONS:
        if text == option_text or lowered == option_text.lower().replace(" ", "_"):
            return intent.value
    return None


def intervention_question(template: str = "en") -> str:
    return _TEMPLATE_BY_NAME.get(str(template).lower(), INTERVENTION_QUESTION_EN)


#: Router action produced by each intervention.  ``offer_scale`` becomes a
#: *start* proposal that carries ``offer="scale"``; ``TurnPolicy`` turns that
#: into ``TurnAction.OFFER_SCALE`` and never into ``START_SCALE``.
_ROUTER_ACTION_BY_INTERVENTION = {
    InterventionIntent.CONTINUE_INTERVIEW: "chat",
    InterventionIntent.OFFER_SCALE: "start_scale",
    InterventionIntent.OFFER_RELAXATION: "recommend_relaxation",
    InterventionIntent.CLARIFY_FIRST: "chat",
}


def intervention_to_router_action(intervention: Any) -> str:
    normalized = normalize_intervention(intervention)
    if normalized is None:
        return "chat"
    return _ROUTER_ACTION_BY_INTERVENTION[InterventionIntent(normalized)]


def offer_kind_for(intervention: Any) -> Optional[OfferKind]:
    normalized = normalize_intervention(intervention)
    if normalized == InterventionIntent.OFFER_SCALE.value:
        return OfferKind.SCALE
    if normalized == InterventionIntent.OFFER_RELAXATION.value:
        return OfferKind.RELAXATION
    return None


__all__ = [
    "DEFAULT_OFFER_POLICY",
    "INTERVENTION_LABELS",
    "INTERVENTION_OPTIONS",
    "INTERVENTION_OPTION_TEXTS",
    "INTERVENTION_QUESTION_EN",
    "INTERVENTION_QUESTION_ZH",
    "InterviewPhase",
    "InterventionIntent",
    "OfferKind",
    "OfferOutcome",
    "OfferPolicy",
    "OfferRecord",
    "OfferState",
    "SCALE_CHOICE_NONE",
    "ScaleCandidate",
    "intervention_question",
    "intervention_to_router_action",
    "interview_phase",
    "normalize_intervention",
    "offer_kind_for",
    "phase_text",
    "rank_scale_candidates",
    "scale_choice_option_texts",
]
