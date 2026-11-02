"""The single construction path for :class:`DecisionRequest`.

There used to be two: ``ConversationPipeline._build_decision_request`` for live
turns and ``TrajectoryRunner._build_request`` for evaluation.  They disagreed in
exactly the places that mattered -- the replay path passed ``recent_history=""``
and derived the symptom summary from the current turn's test annotation, so the
model was asked "is now the right moment, given everything so far" while being
shown nothing but the current sentence, and the rule baseline was handed gold
labels instead of evidence.

Two builders for one contract is how a benchmark ends up measuring itself.  This
module is the contract: every field is filled here, and both callers supply the
same inputs.  ``tests/test_decision_input_consistency.py`` asserts the two paths
produce byte-identical requests for the same dialogue.

``history_mode`` exists so the ablation in §三 of the task can vary *only* the
input, with the model, the template and the policy held fixed:

``minimal``             current utterance + basic runtime state (arm A)
``structured``          + accumulated symptom evidence (arm B)
``structured_history``  + bounded recent dialogue (arm C)

The default is ``structured_history``: the richest arm is the one production
uses, so evaluation defaults to production rather than to the weakest input.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from conversation.interview_state import InterviewState
from conversation.turn_signals import detect_risk_marker, detect_wants_to_talk
from decision.contracts import DecisionRequest
from decision.timing import OfferState

__all__ = ["HISTORY_MODES", "build_decision_request"]

HISTORY_MODES: tuple[str, ...] = ("minimal", "structured", "structured_history")


def build_decision_request(
    *,
    user_text: str,
    round_count: int,
    interview_state: Optional[InterviewState] = None,
    symptom_turns: int = 0,
    runtime: Any = None,
    offer_state: Optional[OfferState] = None,
    registered_scales: Sequence[str] = (),
    session_state: str = "CHATTING",
    clarification_pending: bool = False,
    relaxation_done: bool = False,
    relaxation_active: bool = False,
    history: Optional[str] = None,
    history_mode: str = "structured_history",
    risk_marker: Optional[bool] = None,
    wants_to_talk: Optional[bool] = None,
) -> DecisionRequest:
    """Assemble one immutable decision request.

    ``runtime`` is a ``ScaleRuntime.snapshot()`` result (duck-typed so the
    evaluator does not need the service layer).  Passing ``None`` means "no
    questionnaire state", which is what a trajectory turn without a runtime
    means -- not "unknown".
    """
    mode = str(history_mode or "structured_history")
    if mode not in HISTORY_MODES:
        raise ValueError(
            f"unknown history_mode {history_mode!r}; expected one of {HISTORY_MODES}"
        )

    snapshot = runtime
    state = interview_state or InterviewState()

    if mode == "minimal":
        summary = ""
        turns = 0
        dialogue = ""
    else:
        summary = state.symptom_summary()
        turns = max(0, int(symptom_turns))
        dialogue = (history if history is not None else state.history_text()) if mode == "structured_history" else ""

    return DecisionRequest(
        user_text=user_text,
        recent_history=dialogue or "",
        current_round=max(0, int(round_count)),
        active_scale=getattr(snapshot, "active_scale", None),
        collected_scales={
            name: dict(answers)
            for name, answers in (getattr(snapshot, "answers_by_scale", {}) or {}).items()
        },
        relaxation_done=bool(relaxation_done),
        session_state=str(session_state or "CHATTING"),
        waiting_for_answer=bool(getattr(snapshot, "waiting_for_answer", False)),
        completed_scales=tuple(getattr(snapshot, "completed_scales", ()) or ()),
        game_active=False,
        relaxation_used=bool(relaxation_done),
        offer_state=offer_state or OfferState(),
        registered_scales=tuple(registered_scales or ()),
        administered_scales=tuple(getattr(snapshot, "administered_scales", ()) or ()),
        relaxation_active=bool(relaxation_active),
        clarification_pending=bool(clarification_pending),
        risk_marker=detect_risk_marker(user_text) if risk_marker is None else bool(risk_marker),
        participant_wants_to_talk=(
            detect_wants_to_talk(user_text) if wants_to_talk is None else bool(wants_to_talk)
        ),
        symptom_turns=turns,
        symptom_summary=summary,
        topic_summary=(),
    )
