"""Typed contracts for one decision-layer turn.

Design notes that matter for clinical safety:

1. ``DecisionRequest`` never carries a mutable runtime object.  It is a frozen
   snapshot, so a provider cannot reach back into the session, the scale
   runtime, or the UI.
2. ``RawDecision`` is *unvalidated model output*.  Its numbers are model
   probabilities, not calibrated clinical probabilities; only
   ``DecisionValidator`` decides whether they are usable at all.
3. ``build_observation`` is the only translation into the existing
   ``AgentObservation``/``RouterProposal`` contract.  It degrades to a neutral
   CHAT proposal instead of raising, because a malformed decision must never
   take the conversation down.
4. The decision question is *timing*, not labelling: see
   :mod:`decision.timing`.  ``RawDecision.intervention`` carries the four-way
   answer; ``action`` is only the router projection of it.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from conversation.agent_observation import AgentObservation
from conversation.contracts import RouterAction, RouterProposal
from decision.timing import (
    InterviewPhase,
    InterventionIntent,
    OfferKind,
    OfferState,
    ScaleCandidate,
    intervention_to_router_action,
    interview_phase,
    normalize_intervention,
    offer_kind_for,
    phase_text,
    rank_scale_candidates,
)

# ---------------------------------------------------------------------------
# Modes and backends
# ---------------------------------------------------------------------------


class DecisionMode(str, Enum):
    """How the decision layer participates in one turn."""

    LEGACY = "legacy"
    SHADOW = "shadow"
    JEV = "jev"


class DecisionBackend(str, Enum):
    """Which implementation produces the structured decision."""

    LEGACY_QWEN = "legacy_qwen"
    ANYJEV_DECIDER = "anyjev_decider"
    TACIT = "tacit"
    FAKE = "fake"


class EscalationMode(str, Enum):
    """Where a rejected or failed Jev decision goes."""

    LEGACY = "legacy"
    DETERMINISTIC = "deterministic"


# ---------------------------------------------------------------------------
# Label vocabularies
# ---------------------------------------------------------------------------

# Question 1: user intent.  This is the *decision* vocabulary; it is richer
# than the historical 4-way Agent vocabulary and is mapped down on the border.
INTENT_LABELS: tuple[str, ...] = (
    "chat",
    "symptom",
    "emotion",
    "help_seeking",
    "scale",
    "relaxation",
    "game",
    "end",
    "unclear",
)

# Question 2: emotional cue.  "uncertain" is a first-class answer: the model is
# never forced to pick an emotion.
EMOTION_LABELS: tuple[str, ...] = (
    "calm",
    "sad",
    "anxious",
    "angry",
    "irritable",
    "positive",
    "uncertain",
)

# Question 3: proposed action.  Identical to the Router vocabulary on purpose.
ACTION_LABELS: tuple[str, ...] = tuple(action.value for action in RouterAction)

# Question 4: does this turn need clarification?
CLARIFY_LABELS: tuple[str, ...] = ("no", "yes")

# Question 5: is deeper whole-session reasoning warranted?  Observation only.
COMPLEX_REVIEW_LABELS: tuple[str, ...] = ("no", "yes")

# The historical Agent vocabulary.  ``AgentObservation.intent`` must stay
# inside this set, otherwise existing reporters see an unknown category.
LEGACY_INTENTS: tuple[str, ...] = ("counseling", "entertainment", "chitchat", "relaxation")

INTENT_TO_LEGACY: Mapping[str, str] = {
    "chat": "chitchat",
    "symptom": "counseling",
    "emotion": "counseling",
    "help_seeking": "counseling",
    "scale": "counseling",
    "relaxation": "relaxation",
    "game": "entertainment",
    "end": "counseling",
    "unclear": "counseling",
}

# Project the Jev emotion vocabulary onto the strings the existing emotion
# tracker and reports already understand.
EMOTION_TO_LEGACY: Mapping[str, str] = {
    "calm": "calm",
    "sad": "sad",
    "anxious": "anxious",
    "angry": "angry",
    "irritable": "angry",
    "positive": "happy",
    "uncertain": "neutral",
}

_ENDING_SESSION_STATES = frozenset({"SESSION_ENDING", "SESSION_ENDED"})


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class DecisionRequest(BaseModel):
    """Immutable, read-only view of everything one decision may look at.

    This is a *multi-turn* snapshot.  A single utterance is never enough to
    decide whether an assessment is timely, so the request carries the offer
    history, the registered scale candidates, and the coarse interview phase.

    Every field is a projection of state the pipeline already owns.  Nothing
    here is inferred by a language model, which is what keeps the model from
    "remembering" a scale it never administered or consent the participant
    never gave.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    user_text: str
    recent_history: str = ""
    current_round: int = Field(default=0, ge=0)
    active_scale: Optional[str] = None
    collected_scales: dict[str, dict[Any, Any]] = Field(default_factory=dict)
    relaxation_done: bool = False
    session_state: str = "CHATTING"
    waiting_for_answer: bool = False
    completed_scales: tuple[str, ...] = ()
    game_active: bool = False
    relaxation_used: bool = False

    # -- multi-turn timing state ------------------------------------------
    offer_state: OfferState = Field(default_factory=OfferState)
    registered_scales: tuple[str, ...] = ()
    administered_scales: tuple[str, ...] = ()
    relaxation_active: bool = False
    clarification_pending: bool = False
    risk_marker: bool = False
    #: The participant asked for the floor this turn.  Read as a wish, not as a
    #: diagnosis: it tells the model that an invitation would interrupt.
    participant_wants_to_talk: bool = False
    symptom_turns: int = Field(default=0, ge=0)
    symptom_summary: str = ""
    topic_summary: tuple[str, ...] = ()

    def legacy_kwargs(self) -> dict[str, Any]:
        """Argument mapping for the historical ``AgentService`` call shape."""
        return {
            "user_text": self.user_text,
            "recent_history": self.recent_history,
            "current_round": self.current_round,
            "active_scale": self.active_scale,
            "collected_scales": {name: dict(answers) for name, answers in self.collected_scales.items()},
            "relaxation_done": self.relaxation_done,
        }

    # -- derived, but deterministic ---------------------------------------

    def phase(self) -> InterviewPhase:
        return interview_phase(
            round_count=self.current_round,
            session_state=self.session_state,
            active_scale=self.active_scale,
            offers=self.offer_state,
            relaxation_active=self.relaxation_active,
        )

    def scale_candidates(self) -> tuple[ScaleCandidate, ...]:
        """Scales this session may still offer; empty means "offer nothing"."""
        registered = self.registered_scales or tuple(self.completed_scales)
        return rank_scale_candidates(
            registered=registered,
            completed=self.completed_scales,
            administered=self.administered_scales,
            declined=self.offer_state.declined_scales(),
        )

    def state_summary(self) -> str:
        """Textual state block for a decision model.

        Contains no identifiers and no history beyond what the caller already
        chose to include.  Kept short: the decision model needs the state, not
        a transcript.
        """
        parts = [
            f"轮次第{self.current_round}轮",
            f"访谈阶段{phase_text(self.phase())}",
            f"会话状态{self.session_state}",
        ]
        if self.active_scale:
            parts.append(f"正在进行量表{self.active_scale}")
        else:
            parts.append("当前没有进行中的量表")
        if self.completed_scales:
            parts.append("已完成量表" + "、".join(self.completed_scales))
        if self.collected_scales:
            summary = "、".join(
                f"{name}已采{len(answers)}题" for name, answers in self.collected_scales.items()
            )
            parts.append(summary)
        if self.relaxation_done or self.relaxation_used:
            parts.append("本次会话已完成过放松训练")
        if self.relaxation_active:
            parts.append("正在进行放松训练")
        if self.game_active:
            parts.append("正在游戏中")
        return "；".join(parts)

    def redacted(self) -> dict[str, Any]:
        """Logging-safe projection: metadata only, never the utterance."""
        return {
            "round": self.current_round,
            "session_state": self.session_state,
            "phase": self.phase().value,
            "active_scale": self.active_scale,
            "completed_scales": list(self.completed_scales),
            "text_len": len(self.user_text or ""),
            "history_len": len(self.recent_history or ""),
            "game_active": self.game_active,
            "relaxation_used": self.relaxation_used or self.relaxation_done,
            "offers": self.offer_state.redacted(),
            "scale_candidate_count": len(self.scale_candidates()),
            "risk_marker": self.risk_marker,
        }


# ---------------------------------------------------------------------------
# Raw (unvalidated) decision
# ---------------------------------------------------------------------------


class RawDecision(BaseModel):
    """One provider's output before validation.

    ``observation`` is only populated by the legacy provider, which already
    speaks the historical contract; reusing it verbatim keeps ``legacy`` mode
    byte-for-byte identical to the pre-experiment behaviour.

    ``intervention`` is the authoritative answer when present.  ``action`` is
    kept as the router projection so legacy consumers and the existing
    observation contract keep working, but a provider must not be trusted to
    set ``action`` consistently with ``intervention`` -- the validator
    recomputes it.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    provider: str
    observation: Optional[AgentObservation] = None

    intervention: Optional[str] = None
    offer_target: Optional[str] = None
    scale_choice: Optional[str] = None
    intervention_question: Optional[str] = None

    action: Optional[str] = None
    scale_name: Optional[str] = None
    emotion: str = "uncertain"
    intensity: float = 0.0
    needs_rag: bool = False
    confidence: float = 0.0
    reason: str = ""

    intent_jev: Optional[str] = None
    intent_legacy: str = "counseling"
    clarify: bool = False
    needs_complex_review: bool = False

    probabilities: dict[str, float] = Field(default_factory=dict)
    per_question: dict[str, dict[str, float]] = Field(default_factory=dict)
    latency_ms: float = 0.0
    route: Optional[str] = None
    error: Optional[str] = None
    fallback_used: bool = False

    # -- derived ----------------------------------------------------------

    def normalized_intervention(self) -> Optional[str]:
        return normalize_intervention(self.intervention)

    def effective_action(self) -> str:
        """The router action this decision implies, ignoring self-reported ones."""
        intervention = self.normalized_intervention()
        if intervention is not None:
            return intervention_to_router_action(intervention)
        return (self.action or "").strip().lower()

    def offer_kind(self) -> Optional[OfferKind]:
        return offer_kind_for(self.intervention)

    def diagnostic(self) -> dict[str, Any]:
        """Metadata-only summary for logs and the decision journal."""
        return {
            "provider": self.provider,
            "intervention": self.normalized_intervention(),
            "action": self.action,
            "offer_target": self.offer_target,
            "scale": self.scale_name or self.offer_target,
            "confidence": round(float(self.confidence or 0.0), 4),
            "intent": self.intent_jev or self.intent_legacy,
            "latency_ms": round(float(self.latency_ms or 0.0), 2),
            "route": self.route,
            "fallback_used": self.fallback_used,
            "error": self.error,
        }


class DecidedObservation(BaseModel):
    """Outcome of one coordinator turn."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    observation: AgentObservation
    mode: DecisionMode
    provider: str
    decision: RawDecision
    abstained: bool = False
    reject_reason: Optional[str] = None
    escalated: bool = False
    escalation_source: Optional[str] = None
    shadow: Optional[RawDecision] = None
    comparison: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0


# Alias kept readable at call sites.
DecisionOutcome = DecidedObservation


@runtime_checkable
class DecisionProvider(Protocol):
    """Structural contract every decision backend must satisfy."""

    name: str

    def is_available(self) -> bool:
        ...

    def decide(self, request: DecisionRequest) -> RawDecision:
        ...

    def describe(self) -> dict[str, Any]:
        ...


# ---------------------------------------------------------------------------
# Observation construction
# ---------------------------------------------------------------------------


def map_intent_to_legacy(value: Optional[str]) -> str:
    """Project the decision intent vocabulary onto the historical 4-way set."""
    if not value:
        return "counseling"
    mapped = INTENT_TO_LEGACY.get(str(value).strip().lower())
    if mapped in LEGACY_INTENTS:
        return mapped
    return value if value in LEGACY_INTENTS else "counseling"


def map_emotion_to_legacy(value: Optional[str]) -> str:
    """Project the decision emotion vocabulary onto existing report strings."""
    if not value:
        return "neutral"
    text = str(value).strip().lower()
    if text in EMOTION_TO_LEGACY:
        return EMOTION_TO_LEGACY[text]
    return text


def clamp_unit(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return max(0.0, min(1.0, number))


def build_observation(
    decision: RawDecision,
    *,
    source: str,
    fallback_used: bool = False,
) -> AgentObservation:
    """Translate one raw decision into the existing observation contract.

    Never raises.  An unusable decision degrades to a neutral CHAT proposal
    whose reason records why, so the policy still gets a well-formed
    observation.

    When ``intervention`` is present it wins over ``action``: the four-way
    timing answer is the decision, the router action is only its projection.
    """
    if decision.observation is not None:
        return decision.observation

    reason = decision.reason or ""
    action = RouterAction.CHAT
    scale_name: Optional[str] = None
    offer: Optional[str] = None
    clarify = False
    needs_rag = False
    degraded_reason: Optional[str] = None

    intervention = decision.normalized_intervention()
    if intervention is not None:
        if intervention == InterventionIntent.OFFER_SCALE.value:
            target = (decision.offer_target or decision.scale_name or "").strip()
            if target:
                action = RouterAction.START_SCALE
                offer = OfferKind.SCALE.value
                scale_name = target
            else:
                # An offer with no registered target is not an offer.  Holding
                # the interview is the only defensible degradation.
                degraded_reason = "missing_scale_name"
        elif intervention == InterventionIntent.OFFER_RELAXATION.value:
            action = RouterAction.RECOMMEND_RELAXATION
            offer = OfferKind.RELAXATION.value
        elif intervention == InterventionIntent.CLARIFY_FIRST.value:
            clarify = True
        else:
            needs_rag = True
    else:
        raw_action = (decision.action or "").strip().lower()
        if raw_action in ACTION_LABELS:
            action = RouterAction(raw_action)
        elif raw_action:
            degraded_reason = "unknown_action"

        if action is RouterAction.START_SCALE:
            scale_name = decision.scale_name
        if action is RouterAction.START_SCALE and not scale_name:
            action = RouterAction.CHAT
            degraded_reason = "missing_scale_name"

    try:
        proposal = RouterProposal(
            action=action,
            scale_name=scale_name,
            offer=offer,
            clarify=clarify,
            emotion=map_emotion_to_legacy(decision.emotion),
            intensity=clamp_unit(decision.intensity),
            needs_rag=bool(decision.needs_rag) if action is RouterAction.CHAT else False,
            confidence=clamp_unit(decision.confidence),
            reason=(degraded_reason or reason)[:120],
        )
    except Exception as exc:  # pydantic validation of an unrecognised scale
        proposal = RouterProposal.fallback(f"decision_contract_rejected:{type(exc).__name__}")
        fallback_used = True

    # A degraded proposal is by definition a fallback: the provider's own action
    # could not be honoured, so downstream bookkeeping must see that.
    return AgentObservation(
        proposal=proposal,
        intent=map_intent_to_legacy(decision.intent_legacy or decision.intent_jev),
        fallback_used=bool(fallback_used or decision.fallback_used or degraded_reason),
        source=source,
    )


def action_labels() -> Sequence[str]:
    """Expose the action whitelist for validators and benchmarks."""
    return ACTION_LABELS


def is_ending_state(session_state: str) -> bool:
    return str(session_state) in _ENDING_SESSION_STATES
