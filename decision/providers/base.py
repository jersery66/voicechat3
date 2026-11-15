"""Shared provider scaffolding: errors, question specs, prompt assembly.

The question set is built around one decision: *what should the interview do
next*.  Everything else is auxiliary context, and is labelled as such so that
no later reader mistakes intent or emotion accuracy for the point of the
system.

``intervention``   primary, four-way timing answer (authoritative)
``scale_choice``   conditional, only asked when the answer is "offer a scale"
``strategy``       conversational move for a held interview
``stance``         whether the turn ends in a question
``focus``          which dimension of experience the turn addresses
``emotion``        auxiliary, kept because the observation contract reports it
``intent``         auxiliary, same reason
``complex_review`` auxiliary, a flag for the clinical-review interface
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from decision.contracts import (
    COMPLEX_REVIEW_LABELS,
    EMOTION_LABELS,
    INTENT_LABELS,
    DecisionRequest,
)
from decision.posture import (
    FOCUS_OPTION_TEXTS,
    FOCUS_QUESTION_EN,
    FOCUS_QUESTION_ZH,
    STANCE_OPTION_TEXTS,
    STANCE_QUESTION_EN,
    STANCE_QUESTION_ZH,
    STRATEGY_OPTION_TEXTS,
    STRATEGY_QUESTION_EN,
    STRATEGY_QUESTION_ZH,
)
from decision.timing import (
    INTERVENTION_OPTION_TEXTS,
    SCALE_CHOICE_NONE,
    SCALE_REASON_INCOMPLETE,
    intervention_question,
    phase_text,
    scale_choice_option_texts,
)


class DecisionProviderError(RuntimeError):
    """Base class for every decision-backend failure."""


class DecisionBackendUnavailable(DecisionProviderError):
    """The backend cannot run here (missing package, model, or endpoint)."""


class DecisionTransportError(DecisionProviderError):
    """The backend was reachable in principle but the call failed."""


@dataclass(frozen=True)
class QuestionSpec:
    """One typed decision question in the AnyJev sense.

    ``name`` is the key used in the request/response payload and in
    ``RawDecision.per_question``.  ``options`` is the label space; the first
    label is never privileged, because AnyJev's L0 readout removes option-order
    bias and we additionally check order sensitivity in the benchmark.
    """

    name: str
    text: str
    options: tuple[str, ...]
    primary: bool = False

    def payload(self) -> dict[str, Any]:
        return {"name": self.name, "question": self.text, "options": list(self.options)}


def build_questions(template: str = "en") -> tuple[QuestionSpec, ...]:
    """Build the question set for one prompt template.

    ``template="en"`` keeps the English wording, which is what the official
    Tacit checkpoint was trained against; ``"zh"`` exists so the difference can
    be measured on held-out trajectories rather than guessed at.
    """
    return (
        QuestionSpec(
            name="intervention",
            text=intervention_question(template),
            options=INTERVENTION_OPTION_TEXTS,
            primary=True,
        ),
        # The two posture questions only matter when the intervention answer is
        # "continue interviewing", but they are asked unconditionally: making
        # them conditional would require a second model round trip on almost
        # every turn, and a task-changing answer simply drops the result.
        QuestionSpec(
            name="strategy",
            text=(
                STRATEGY_QUESTION_EN if template == "en" else STRATEGY_QUESTION_ZH
            ),
            options=STRATEGY_OPTION_TEXTS,
        ),
        QuestionSpec(
            name="stance",
            text=STANCE_QUESTION_EN if template == "en" else STANCE_QUESTION_ZH,
            options=STANCE_OPTION_TEXTS,
        ),
        # Which dimension the turn addresses.  Also asked unconditionally for
        # the same reason as the posture pair: a second round trip on every
        # task-changing turn would cost more than the wasted answer.
        QuestionSpec(
            name="focus",
            text=FOCUS_QUESTION_EN if template == "en" else FOCUS_QUESTION_ZH,
            options=FOCUS_OPTION_TEXTS,
        ),
        QuestionSpec(
            name="emotion",
            text=(
                "What is the participant's dominant emotion in this turn? "
                "Choose 'uncertain' if it is not clear."
                if template == "en"
                else "这句话表达的主要情绪是哪一类？如果不确定，选择 uncertain。"
            ),
            options=EMOTION_LABELS,
        ),
        QuestionSpec(
            name="intent",
            text=(
                "What is the participant mainly doing in this turn?"
                if template == "en"
                else "来访者这一轮发言的主要意图是哪一类？"
            ),
            options=INTENT_LABELS,
        ),
        QuestionSpec(
            name="complex_review",
            text=(
                "Does this turn need deeper whole-session clinical review? "
                "Observation only; it authorises nothing."
                if template == "en"
                else "这一轮是否值得转入更深入的整体评估（仅供观察，不授权任何临床结论）？"
            ),
            options=COMPLEX_REVIEW_LABELS,
        ),
    )


#: Default (English) question set.  ``intervention`` is first on purpose: it is
#: the decision, and the rest are context.
DECISION_QUESTIONS: tuple[QuestionSpec, ...] = build_questions("en")

QUESTIONS_BY_NAME = {spec.name: spec for spec in DECISION_QUESTIONS}

PRIMARY_QUESTION = QUESTIONS_BY_NAME["intervention"]


def build_scale_choice_question(request: DecisionRequest) -> Optional[QuestionSpec]:
    """The conditional "which questionnaire" question.

    Returns ``None`` when the runtime has no candidate, because asking a model
    to pick from an empty list is how systems end up inventing scale names.
    """
    candidates = request.scale_candidates()
    if not candidates:
        return None
    return QuestionSpec(
        name="scale_choice",
        text=(
            "If a questionnaire is offered now, which one best fits the current "
            "interview focus? Choose 'No scale for now' if none is appropriate."
        ),
        options=scale_choice_option_texts(candidates),
    )


def _candidate_note(candidate: Any) -> str:
    """Human-readable reason for one candidate's position in the list."""
    if getattr(candidate, "reason", "") == SCALE_REASON_INCOMPLETE:
        return "未完成，可续做"
    if int(getattr(candidate, "evidence", 0) or 0) > 0:
        return "谈话内容更吻合"
    return "未开展"


def build_state_text(request: DecisionRequest, *, max_history_chars: int = 1200) -> str:
    """Assemble the decision-state text for one turn.

    Every line is a projection of state the pipeline already owns: round,
    phase, assessment history, invitations and their answers, and the
    deterministic symptom counters.  Nothing here asks the model to remember
    anything the runtime did not record.

    The state block is the only place where participant wording enters a
    decision model.  It is never logged and never persisted by this layer.
    """
    parts = [f"【会话状态】{request.state_summary()}"]

    # The interview's objective, stated once.  Without it the stance question is
    # answered against a generic conversational norm ("listen more, ask less"),
    # and a model trained on counselling dialogue answers "do not ask" on most
    # intake turns -- which is a good therapist and a broken intake.  The
    # listening option stays legitimate: the last sentence says so.
    parts.append(
        "【访谈目标】这是摄入性访谈，结束时需要覆盖睡眠、情绪、兴趣与愉悦、精力与疲劳、"
        "注意力与做事、食欲与体重、身体不适、日常生活与人际。"
        "提问轮要先落在刚说的那件事上；那个方面最近刚问过，再换一个没问过的。"
        "来访者正说得深入时，偶尔只回应、不提问是合适的。"
    )

    candidates = request.scale_candidates()
    if candidates:
        # The order is already evidence-first, but the model is told *why* the
        # first candidate is first.  A bare list makes the choice look
        # arbitrary and a small model then simply takes the head of the list
        # for every participant; naming the reason lets it override the order
        # when the interview has clearly moved elsewhere.
        described = "、".join(
            f"{candidate.name}({_candidate_note(candidate)})"
            for candidate in candidates
        )
        parts.append(
            f"【可提供的量表】{described}（按与本次谈话的吻合程度从高到低排列）"
        )
    else:
        parts.append("【可提供的量表】没有可提供的量表")

    parts.append("【既往建议】" + "；".join(request.offer_state.summary_parts()))

    if request.symptom_summary:
        parts.append(f"【症状线索】{request.symptom_summary}")
    elif request.symptom_turns:
        parts.append(f"【症状线索】已有 {request.symptom_turns} 轮出现症状主诉")

    if request.topic_summary:
        parts.append("【已谈内容】" + "；".join(request.topic_summary))

    # Coverage is the feedback loop for the focus question.  Rendered as two
    # explicit lists rather than a percentage, because "还没问过：食欲、身体" is
    # an instruction the model can act on and "覆盖率 44%" is not.
    from decision.posture import FOCUS_LABELS, focus_name

    covered = {
        label: int(count)
        for label, count in (request.focus_coverage or {}).items()
        if label in FOCUS_LABELS and int(count) > 0
    }
    if covered:
        ordered = sorted(covered.items(), key=lambda item: (-item[1], item[0]))
        parts.append(
            "【已问过的方面】"
            + "、".join(f"{focus_name(label)}×{count}" for label, count in ordered)
        )
        missing = [focus_name(label) for label in FOCUS_LABELS if label not in covered]
        if missing:
            parts.append("【还没问过的方面】" + "、".join(missing))

    if request.clarification_pending:
        parts.append("【待澄清】上一轮存在需要确认的信息，尚未澄清")

    if request.participant_wants_to_talk:
        # A wish, not an assessment: it only tells the model that inviting an
        # activity right now would interrupt the participant.
        parts.append("【来访者意愿】本轮明确希望继续表达、希望被听完")

    if request.risk_marker:
        # Stated as a routing fact, never as a diagnosis.  The safety path owns
        # what happens next; the decision layer only loses the right to offer
        # an activity here.
        parts.append("【安全提示】本轮出现自伤或自杀相关表述，须由独立安全流程处理")

    parts.append(f"【访谈阶段】{phase_text(request.phase())}")

    history = (request.recent_history or "").strip()
    if len(history) > max_history_chars:
        history = history[-max_history_chars:]
    if history:
        parts.append(f"【最近对话】\n{history}")
    parts.append(f"【本轮发言】{request.user_text}")
    return "\n".join(parts)


def require_loopback(endpoint: str, *, allow_remote: bool = False) -> str:
    """Reject non-loopback decision endpoints unless explicitly permitted.

    The AnyJev gateway has no authentication of its own, so an interview
    transcript sent to a non-loopback address would be an unauthenticated leak.
    """
    from urllib.parse import urlparse

    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise DecisionProviderError(f"decision endpoint has no host: {endpoint!r}")
    if allow_remote:
        return endpoint
    if host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:
        return endpoint
    raise DecisionProviderError(
        "decision endpoint must be loopback (127.0.0.1/localhost/::1); "
        f"got {host!r}. Set decision.allow_remote_endpoint to true only for a "
        "network you control end to end."
    )


def labels_for(name: str) -> Sequence[str]:
    return QUESTIONS_BY_NAME[name].options


def resolve_scale_choice(answer: Any, request: DecisionRequest) -> Optional[str]:
    """Map the model's "which scale" answer onto a real candidate, or None."""
    if answer is None:
        return None
    text = str(answer).strip()
    if not text or text == SCALE_CHOICE_NONE:
        return None
    candidate_names = {candidate.name for candidate in request.scale_candidates()}
    if text in candidate_names:
        return text
    # The readout may return a label with different casing or spacing.
    squashed = text.upper().replace(" ", "")
    for name in candidate_names:
        if squashed == name.upper().replace(" ", ""):
            return name
    return None


#: Stage-2 outcomes for the conditional scale-target resolution.
SCALE_TARGET_RESOLVED = "resolved"          # the model named a legal candidate
SCALE_TARGET_DETERMINISTIC = "deterministic"  # unusable answer, one legal candidate
SCALE_TARGET_ABSTAINED = "model_abstained"  # the model chose "No scale for now"
SCALE_TARGET_UNRESOLVABLE = "unresolvable"  # unusable answer, several candidates
SCALE_TARGET_NO_CANDIDATES = "no_candidates"  # nothing legal left to offer


def resolve_scale_target(
    request: "DecisionRequest",
    scale_choice: Optional[tuple[Any, dict[str, float]]],
) -> tuple[Optional[str], str]:
    """Stage 2 of the scale decision: resolve *which* instrument, if any.

    Runs only after stage 1 answered OFFER_SCALE.  The candidate list comes
    from the same registry production starts instruments from, already
    filtered by completion, administration and prior declines.

    Returns ``(target, outcome)``:

    ``resolved``        the model named a legal candidate.
    ``deterministic``   the answer was unusable or absent, but exactly one
                        legal candidate remains -- the protocol picks it, no
                        model invention involved.
    ``model_abstained`` the model explicitly chose "No scale for now".  This
                        is a real answer, not a failure: it is never
                        overridden, even when a unique candidate exists,
                        because replacing a deliberate abstention with a
                        fabricated target is how consent erodes.
    ``unresolvable``    unusable answer with several legal candidates and no
                        basis to pick one -- the caller routes this to
                        clarification, never to a silent continue.
    ``no_candidates``   nothing legal left; any offer would be a fabrication.
    """
    candidates = request.scale_candidates()
    if not candidates:
        return None, SCALE_TARGET_NO_CANDIDATES
    answer = scale_choice[0] if scale_choice else None
    target = resolve_scale_choice(answer, request)
    if target is not None:
        return target, SCALE_TARGET_RESOLVED
    text = str(answer).strip() if answer is not None else ""
    if text == SCALE_CHOICE_NONE:
        return None, SCALE_TARGET_ABSTAINED
    if len(candidates) == 1:
        return candidates[0].name, SCALE_TARGET_DETERMINISTIC
    return None, SCALE_TARGET_UNRESOLVABLE


def compose_decision(
    *,
    provider: str,
    request: "DecisionRequest",
    answers: dict[str, tuple[str, dict[str, float]]],
    latency_ms: float,
    route: str,
    reason: str,
    scale_choice: Optional[tuple[str, dict[str, float]]] = None,
) -> "RawDecision":
    """Turn a bag of question answers into one :class:`RawDecision`.

    Shared by every backend so that the mapping from "option text" to
    "intervention label" happens exactly once.  A model answer outside the
    four-way vocabulary is preserved verbatim rather than coerced, so the
    validator can reject it with an attributable reason.
    """
    from decision.contracts import RawDecision
    from decision.timing import (
        InterventionIntent,
        intervention_to_router_action,
        normalize_intervention,
    )

    intervention_raw, intervention_probs = answers["intervention"]
    intervention = normalize_intervention(intervention_raw)
    probabilities: dict[str, float] = {}
    for label, value in intervention_probs.items():
        probabilities[normalize_intervention(label) or str(label)] = float(value)

    target: Optional[str] = None
    target_outcome: Optional[str] = None
    if intervention == InterventionIntent.OFFER_SCALE.value:
        # Stage 2 runs whenever stage 1 says "offer a scale": resolve the
        # target now so the validator can separate "timing wrong" from
        # "timing right but instrument undecided".
        target, target_outcome = resolve_scale_target(request, scale_choice)

    intent = str(answers.get("intent", ("counseling", {}))[0]).strip().lower()
    emotion = str(answers.get("emotion", ("uncertain", {}))[0]).strip().lower()
    complex_review = str(answers.get("complex_review", ("no", {}))[0]).strip().lower()

    from decision.posture import normalize_focus, normalize_stance, normalize_strategy

    strategy = normalize_strategy(answers.get("strategy", (None, {}))[0])
    stance = normalize_stance(answers.get("stance", (None, {}))[0])
    focus = normalize_focus(answers.get("focus", (None, {}))[0])

    confidence = 0.0
    if intervention is not None:
        confidence = float(probabilities.get(intervention, 0.0))

    action = intervention_to_router_action(intervention) if intervention else None
    return RawDecision(
        provider=provider,
        intervention=intervention_raw,
        offer_target=target,
        scale_target_outcome=target_outcome,
        scale_choice=scale_choice[0] if scale_choice is not None else None,
        action=action,
        scale_name=target,
        emotion=emotion,
        intensity=0.0,
        needs_rag=intervention == InterventionIntent.CONTINUE_INTERVIEW.value,
        confidence=confidence,
        reason=reason,
        intent_jev=intent,
        intent_legacy=intent,
        clarify=intervention == InterventionIntent.CLARIFY_FIRST.value,
        needs_complex_review=complex_review == "yes",
        strategy=strategy,
        stance=stance,
        focus=focus,
        probabilities=probabilities,
        per_question={name: dict(probs) for name, (_answer, probs) in answers.items()},
        latency_ms=latency_ms,
        route=route,
    )
