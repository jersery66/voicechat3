"""Complex end-of-interview review and auxiliary clinical analysis — interface only.

This module deliberately ships **no clinical logic and no automatic decision**.
It defines:

1. the split between *objective* end conditions (deterministic, already owned by
   ``SessionEngine``/``TurnPolicy``/``ScaleRuntime``) and the questions that
   genuinely need language-model reasoning;
2. the typed request/result a future ``ClinicalReviewService`` must return;
3. a disabled default implementation, so nothing in the runtime path can start
   producing diagnoses by accident.

Hard rules encoded here:

- ``ClinicalReviewResult.requires_human_review`` is always ``True``; the model
  cannot mark its own output as reviewable-free.
- ``ClinicalReviewResult.not_for_diagnosis`` is always ``True``.
- A review result is never consumed by ``TurnPolicy``, ``ScaleRuntime``, or the
  report generator in this version.  The interface exists so the wiring can be
  reviewed before it is trusted.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

POLARITY_PRESENT = "present"
POLARITY_DENIED = "denied"
POLARITY_UNCERTAIN = "uncertain"

EVIDENCE_POLARITIES = frozenset({POLARITY_PRESENT, POLARITY_DENIED, POLARITY_UNCERTAIN})

# Deterministic end conditions.  These are evaluated without a model.
OBJECTIVE_USER_EXPLICIT = "user_explicit_end"
OBJECTIVE_DURATION_LIMIT = "duration_limit_reached"
OBJECTIVE_PROTOCOL_COMPLETE = "scale_protocol_complete"

# Questions that may be sent to the dialogue model for a *draft opinion*.
REVIEW_MISSING_INFORMATION = "missing_information"
REVIEW_CONTRADICTION = "unresolved_contradiction"
REVIEW_INSUFFICIENT_COVERAGE = "insufficient_topic_coverage"
REVIEW_SUMMARY_READINESS = "summary_readiness"


class EvidencePolarity(str, Enum):
    """Direction of one piece of evidence.

    The distinction matters clinically: "没有睡不着" is a denial, not an
    absence of information, and a model that collapses the two over-reads risk.
    """

    PRESENT = POLARITY_PRESENT
    DENIED = POLARITY_DENIED
    UNCERTAIN = POLARITY_UNCERTAIN


class ReviewEvidence(BaseModel):
    """One traceable evidence item.

    ``turn_id`` is an opaque turn number, never a quote: raw interview text is
    not part of this contract, so the review artifact cannot leak it.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    domain: str
    polarity: EvidencePolarity
    turn_id: Optional[int] = Field(default=None, ge=1)
    source: Literal["explicit_statement", "denial", "inference", "scale_item"] = "explicit_statement"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ClinicalReviewRequest(BaseModel):
    """Everything a review service may look at.

    Contains no free text from the interview: only structured evidence, scale
    summaries, and coverage bookkeeping.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    session_id: str = Field(description="opaque local session identifier")
    round_count: int = Field(default=0, ge=0)
    duration_minutes: Optional[float] = Field(default=None, ge=0.0)
    completed_scales: dict[str, dict[str, Any]] = Field(default_factory=dict)
    evidence: tuple[ReviewEvidence, ...] = ()
    open_questions: tuple[str, ...] = ()
    objective_end_conditions: tuple[str, ...] = ()

    def evidence_by_domain(self) -> dict[str, list[ReviewEvidence]]:
        grouped: dict[str, list[ReviewEvidence]] = {}
        for item in self.evidence:
            grouped.setdefault(item.domain, []).append(item)
        return grouped


class ClinicalReviewResult(BaseModel):
    """A draft for a qualified professional to review — never a conclusion."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    summary_draft: str = ""
    structured_analysis: dict[str, Any] = Field(default_factory=dict)
    evidence_summary: tuple[dict[str, Any], ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    contradictions: tuple[str, ...] = ()
    uncertainty_note: str = ""
    disabled: bool = False
    generated_by: str = "disabled"
    model: Optional[str] = None
    requires_human_review: bool = True
    not_for_diagnosis: bool = True

    @model_validator(mode="after")
    def _enforce_review_flags(self) -> "ClinicalReviewResult":
        if self.requires_human_review is not True:
            raise ValueError("a clinical review draft always requires human review")
        if self.not_for_diagnosis is not True:
            raise ValueError("a clinical review draft is never a diagnosis")
        return self


class EndSessionAssessment(BaseModel):
    """Deterministic split of *why* an interview might end."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    objective_conditions: tuple[str, ...] = ()
    requires_complex_review: tuple[str, ...] = ()
    decided_by: Literal["deterministic"] = "deterministic"

    @property
    def has_objective_condition(self) -> bool:
        return bool(self.objective_conditions)


def assess_end_conditions(
    *,
    user_explicit_end: bool = False,
    duration_limit_reached: bool = False,
    scale_protocol_complete: bool = False,
    round_count: int = 0,
    min_rounds: int = 0,
    open_questions: tuple[str, ...] = (),
    contradictions: tuple[str, ...] = (),
) -> EndSessionAssessment:
    """Split end conditions without asking a model anything.

    Objective conditions are the only ones that may *end* a session, and this
    function does not end it either — it reports what it saw.  The rest are
    requests for a review draft, which a human must accept.
    """
    objective: list[str] = []
    if user_explicit_end:
        objective.append(OBJECTIVE_USER_EXPLICIT)
    if duration_limit_reached:
        objective.append(OBJECTIVE_DURATION_LIMIT)
    if scale_protocol_complete:
        objective.append(OBJECTIVE_PROTOCOL_COMPLETE)

    review: list[str] = []
    if user_explicit_end or duration_limit_reached or scale_protocol_complete:
        if open_questions:
            review.append(REVIEW_MISSING_INFORMATION)
        if contradictions:
            review.append(REVIEW_CONTRADICTION)
        if round_count < max(0, int(min_rounds)):
            review.append(REVIEW_INSUFFICIENT_COVERAGE)
        review.append(REVIEW_SUMMARY_READINESS)

    return EndSessionAssessment(
        objective_conditions=tuple(objective),
        requires_complex_review=tuple(review),
    )


@runtime_checkable
class ClinicalReviewService(Protocol):
    """Contract for a future review service."""

    def is_enabled(self) -> bool:
        ...

    def review(self, request: ClinicalReviewRequest) -> ClinicalReviewResult:
        ...


class DisabledClinicalReviewService:
    """The default.  Returns a well-formed draft that says it did nothing."""

    def is_enabled(self) -> bool:
        return False

    def review(self, request: ClinicalReviewRequest) -> ClinicalReviewResult:
        return ClinicalReviewResult(
            summary_draft="",
            uncertainty_note="review disabled by configuration",
            unresolved_questions=tuple(request.open_questions),
            disabled=True,
            generated_by="disabled",
        )


class QwenClinicalReviewService:
    """Review service that delegates to an injected text-completion callable.

    No network client is constructed here: the caller supplies ``complete``
    (system prompt, user prompt) -> text.  That keeps this module testable, keeps
    the dialog model config in one place, and makes it impossible for this class
    to reach a paid API by itself.
    """

    SYSTEM_PROMPT = (
        "你是心理访谈记录的结构化整理助手。你只整理证据，不做诊断、不下结论、不给治疗建议。"
        "输出 JSON，字段：summary_draft, evidence_summary, contradictions, "
        "unresolved_questions, uncertainty_note。"
        "如果证据不足，必须在 uncertainty_note 中明确指出，不得推测。"
    )

    def __init__(
        self,
        complete: Callable[[str, str], str],
        *,
        model: Optional[str] = None,
        enabled: bool = False,
    ) -> None:
        self._complete = complete
        self.model = model
        self._enabled = bool(enabled)

    def is_enabled(self) -> bool:
        return self._enabled

    def review(self, request: ClinicalReviewRequest) -> ClinicalReviewResult:
        if not self._enabled:
            return DisabledClinicalReviewService().review(request)
        import json

        payload = {
            "round_count": request.round_count,
            "duration_minutes": request.duration_minutes,
            "completed_scales": request.completed_scales,
            "evidence": [item.model_dump(mode="json") for item in request.evidence],
            "open_questions": list(request.open_questions),
            "objective_end_conditions": list(request.objective_end_conditions),
        }
        text = self._complete(
            self.SYSTEM_PROMPT,
            "请整理以下结构化访谈证据：\n" + json.dumps(payload, ensure_ascii=False),
        )
        parsed: dict[str, Any] = {}
        try:
            parsed = json.loads(_strip_fence(text))
        except Exception:
            parsed = {}

        return ClinicalReviewResult(
            summary_draft=str(parsed.get("summary_draft") or text or "")[:4000],
            structured_analysis=parsed if parsed else {"raw": str(text or "")[:4000]},
            evidence_summary=tuple(parsed.get("evidence_summary") or ()),
            unresolved_questions=tuple(parsed.get("unresolved_questions") or request.open_questions),
            contradictions=tuple(parsed.get("contradictions") or ()),
            uncertainty_note=str(parsed.get("uncertainty_note") or "模型草稿，未经人工核对")[:1000],
            disabled=False,
            generated_by="qwen_clinical_review",
            model=self.model,
        )


def _strip_fence(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()
