"""AnyJev ``Decider`` / gateway adapter.

Two transports, one label space:

``server`` (default)
    Talks to ``python -m anyjev.serve`` (``POST /v1/decide``).  The gateway
    listens on 127.0.0.1 with no authentication of its own, so the transport
    refuses non-loopback endpoints.  This is the deployment path this project
    can actually run today.

``inprocess``
    Builds an ``anyjev.Decider`` over a local Hugging Face checkpoint.  It is
    implemented but unexecuted here: this machine has no decision checkpoint
    installed and the project forbids downloading one implicitly.

The five questions are asked in one batch when possible.  A gateway that
answers only single decisions is handled by falling back to sequential calls;
the route field records which happened.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from decision.contracts import DecisionRequest, RawDecision
from decision.providers.base import (
    DECISION_QUESTIONS,
    DecisionBackendUnavailable,
    DecisionTransportError,
    QuestionSpec,
    build_scale_choice_question,
    build_state_text,
    compose_decision,
)
from decision.timing import InterventionIntent, normalize_intervention
from decision.providers.http_transport import HttpDecisionTransport

_PROBABILITY_KEYS = ("probs", "probabilities", "distribution", "probs_by_option")
_ANSWER_KEYS = ("answer", "decision", "label", "choice", "selected")
_LIST_KEYS = ("items", "results", "decisions", "outputs")


class _ShapeError(ValueError):
    """The gateway answered, but not in a shape this adapter understands."""


def _extract_decision(payload: Any) -> tuple[str, dict[str, float], Optional[str]]:
    """Pull (answer, probabilities, route) out of one gateway response object."""
    if not isinstance(payload, dict):
        raise _ShapeError(f"expected an object, got {type(payload).__name__}")

    answer = None
    for key in _ANSWER_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            answer = value.strip()
            break

    probabilities: dict[str, float] = {}
    for key in _PROBABILITY_KEYS:
        value = payload.get(key)
        if isinstance(value, dict) and value:
            probabilities = {str(k): float(v) for k, v in value.items()}
            break

    route = payload.get("route")
    if answer is None or not probabilities:
        raise _ShapeError(f"response has no usable answer/probabilities: keys={sorted(payload)}")
    return answer, probabilities, (str(route) if route else None)


class AnyJevDecisionProvider:
    """Jev-style decision through AnyJev's Decider or its HTTP gateway."""

    name = "anyjev_decider"

    def __init__(
        self,
        *,
        transport: str = "server",
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 5.0,
        allow_remote_endpoint: bool = False,
        allow_model_download: bool = False,
        batch: bool = True,
        decider_factory: Optional[Any] = None,
    ) -> None:
        self.transport = str(transport or "server").strip().lower()
        if self.transport not in {"server", "inprocess"}:
            raise ValueError("anyjev transport must be 'server' or 'inprocess'")
        self.endpoint = endpoint
        self.model = model
        self.timeout = float(timeout)
        self.allow_remote_endpoint = bool(allow_remote_endpoint)
        self.allow_model_download = bool(allow_model_download)
        self.batch = bool(batch)
        self._http: Optional[HttpDecisionTransport] = None
        self._decider = None
        self._decider_factory = decider_factory

        if self.transport == "server":
            if not endpoint:
                raise DecisionBackendUnavailable(
                    "anyjev server transport needs decision.endpoint "
                    "(e.g. http://127.0.0.1:8100)"
                )
            self._http = HttpDecisionTransport(
                endpoint,
                timeout=self.timeout,
                allow_remote=self.allow_remote_endpoint,
            )

    # -- provider protocol -------------------------------------------------

    def is_available(self) -> bool:
        if self.transport == "server":
            return self._http is not None
        try:
            return self._ensure_decider() is not None
        except DecisionBackendUnavailable:
            return False

    def describe(self) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "provider": self.name,
            "transport": self.transport,
            "model": self.model,
            "batch": self.batch,
        }
        if self._http is not None:
            detail.update(self._http.describe())
        return detail

    def decide(self, request: DecisionRequest) -> RawDecision:
        state = build_state_text(request)
        started = time.perf_counter()

        if self.transport == "inprocess":
            answers = self._decide_inprocess(state, DECISION_QUESTIONS)
            route = "inprocess"
        else:
            answers, route = self._decide_over_http(state, DECISION_QUESTIONS)

        scale_choice = None
        if normalize_intervention(answers["intervention"][0]) == InterventionIntent.OFFER_SCALE.value:
            spec = build_scale_choice_question(request)
            if spec is not None:
                if self.transport == "inprocess":
                    extra = self._decide_inprocess(state, (spec,))
                else:
                    extra, _extra_route = self._decide_over_http(state, (spec,))
                answers.update(extra)
                scale_choice = extra[spec.name]

        latency_ms = (time.perf_counter() - started) * 1000.0
        return compose_decision(
            provider=self.name,
            request=request,
            answers=answers,
            latency_ms=latency_ms,
            route=route,
            reason=f"anyjev:{route}",
            scale_choice=scale_choice,
        )

    # -- transports --------------------------------------------------------

    def _decide_over_http(
        self, state: str, specs: tuple[QuestionSpec, ...] = DECISION_QUESTIONS
    ) -> tuple[dict[str, tuple[str, dict[str, float]]], str]:
        assert self._http is not None
        if self.batch:
            try:
                payload = self._http.post(
                    {"items": [{"state": state, **spec.payload()} for spec in specs]}
                )
                return self._parse_batch(payload, specs), "batch"
            except _ShapeError:
                # The gateway may only implement single decisions.  Falling back
                # is cheap and local; record which path ran.
                pass
        answers: dict[str, tuple[str, dict[str, float]]] = {}
        for spec in specs:
            payload = self._http.post({"state": state, **spec.payload()})
            answer, probabilities, _route = _extract_decision(payload)
            answers[spec.name] = (answer, probabilities)
        return answers, "sequential"

    def _parse_batch(
        self, payload: Any, specs: tuple[QuestionSpec, ...] = DECISION_QUESTIONS
    ) -> dict[str, tuple[str, dict[str, float]]]:
        if isinstance(payload, dict):
            for key in _LIST_KEYS:
                value = payload.get(key)
                if isinstance(value, list):
                    payload = value
                    break
        if not isinstance(payload, list):
            raise _ShapeError("batch response is not a list")
        if len(payload) != len(specs):
            raise _ShapeError(
                f"batch response has {len(payload)} items, expected {len(specs)}"
            )
        answers: dict[str, tuple[str, dict[str, float]]] = {}
        for spec, item in zip(specs, payload):
            answer, probabilities, _route = _extract_decision(item)
            answers[spec.name] = (answer, probabilities)
        return answers

    def _decide_inprocess(
        self, state: str, specs: tuple[QuestionSpec, ...] = DECISION_QUESTIONS
    ) -> dict[str, tuple[str, dict[str, float]]]:
        decider = self._ensure_decider()
        if decider is None:
            raise DecisionBackendUnavailable(
                "anyjev Decider is not available (package or checkpoint missing)"
            )
        try:
            from anyjev import Question
        except Exception as exc:  # pragma: no cover - optional dependency
            raise DecisionBackendUnavailable(f"anyjev is not installed: {exc}") from exc

        questions = [
            Question.choice(spec.text, list(spec.options), name=spec.name)
            for spec in specs
        ]
        results = decider.decide(state, questions)
        answers: dict[str, tuple[str, dict[str, float]]] = {}
        for spec in specs:
            result = results.get(spec.name) if isinstance(results, dict) else None
            if result is None:
                raise DecisionBackendUnavailable(f"anyjev returned no result for {spec.name!r}")
            distribution = getattr(result, "distribution", None)
            answer = getattr(result, "answer", None)
            if not isinstance(distribution, dict) or not answer:
                raise DecisionBackendUnavailable(f"anyjev result for {spec.name!r} is unusable")
            answers[spec.name] = (str(answer), {str(k): float(v) for k, v in distribution.items()})
        return answers

    def _ensure_decider(self):
        if self._decider is not None:
            return self._decider
        if self._decider_factory is not None:
            self._decider = self._decider_factory(self)
            return self._decider
        if not self.model:
            return None
        looks_local = any(sep in self.model for sep in ("/", "\\")) and not self.model.startswith(
            ("Qwen/", "meta-llama/", "google/", "mistralai/")
        )
        if not looks_local and not self.allow_model_download:
            # A bare Hub id would trigger an implicit multi-GB download.
            raise DecisionBackendUnavailable(
                f"decision.model {self.model!r} looks like a Hugging Face repo id; "
                "set decision.allow_model_download to true or point model at a local path"
            )
        try:
            from anyjev import Decider
            from anyjev.backends.hf import HFBackend
        except Exception as exc:  # pragma: no cover - optional dependency
            raise DecisionBackendUnavailable(
                f"anyjev[hf] is not installed: {exc}. Install it in an isolated env."
            ) from exc
        self._decider = Decider(HFBackend(self.model))
        return self._decider
