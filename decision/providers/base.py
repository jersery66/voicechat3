"""Shared provider scaffolding: errors, question specs, prompt assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from decision.contracts import (
    ACTION_LABELS,
    CLARIFY_LABELS,
    COMPLEX_REVIEW_LABELS,
    EMOTION_LABELS,
    INTENT_LABELS,
    DecisionRequest,
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

    def payload(self) -> dict[str, Any]:
        return {"name": self.name, "question": self.text, "options": list(self.options)}


# The five observation questions.  Only ``action`` is executable downstream, and
# even that is a *proposal* that TurnPolicy re-judges.
DECISION_QUESTIONS: tuple[QuestionSpec, ...] = (
    QuestionSpec(
        name="intent",
        text="来访者这一轮发言的主要意图是哪一类？",
        options=INTENT_LABELS,
    ),
    QuestionSpec(
        name="emotion",
        text="这句话表达的主要情绪是哪一类？如果不确定，选择 uncertain。",
        options=EMOTION_LABELS,
    ),
    QuestionSpec(
        name="action",
        text="对话系统这一轮最合适的下一步动作是哪一个？",
        options=ACTION_LABELS,
    ),
    QuestionSpec(
        name="clarify",
        text="这句话是否语义含糊、自相矛盾或可能被语音识别转录错了，需要澄清？",
        options=CLARIFY_LABELS,
    ),
    QuestionSpec(
        name="complex_review",
        text="这一轮是否值得转入更深入的整体评估（仅供观察，不授权任何临床结论）？",
        options=COMPLEX_REVIEW_LABELS,
    ),
)

QUESTIONS_BY_NAME = {spec.name: spec for spec in DECISION_QUESTIONS}


def build_state_text(request: DecisionRequest, *, max_history_chars: int = 800) -> str:
    """Assemble the decision-state text for one turn.

    The state block is the only place where participant wording enters a
    decision model.  It is never logged and never persisted by this layer.
    """
    history = (request.recent_history or "").strip()
    if len(history) > max_history_chars:
        history = history[-max_history_chars:]
    parts = [f"【会话状态】{request.state_summary()}"]
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
