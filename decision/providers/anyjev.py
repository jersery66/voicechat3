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
    build_state_text,
)
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
            answers = self._decide_inprocess(state)
            route = "inprocess"
        else:
            answers, route = self._decide_over_http(state)

        latency_ms = (time.perf_counter() - started) * 1000.0
        return self._assemble(answers, latency_ms=latency_ms, route=route)

    # -- transports --------------------------------------------------------

    def _decide_over_http(self, state: str) -> tuple[dict[str, tuple[str, dict[str, float]]], str]:
        assert self._http is not None
        if self.batch:
            try:
                payload = self._http.post(
                    {
                        "items": [
                            {"state": state, **spec.payload()} for spec in DECISION_QUESTIONS
                        ]
                    }
                )
                return self._parse_batch(payload), "batch"
            except _ShapeError:
                # The gateway may only implement single decisions.  Falling back
                # is cheap and local; record which path ran.
                pass
        answers: dict[str, tuple[str, dict[str, float]]] = {}
        for spec in DECISION_QUESTIONS:
            payload = self._http.post({"state": state, **spec.payload()})
            answer, probabilities, _route = _extract_decision(payload)
            answers[spec.name] = (answer, probabilities)
        return answers, "sequential"

    def _parse_batch(self, payload: Any) -> dict[str, tuple[str, dict[str, float]]]:
        if isinstance(payload, dict):
            for key in _LIST_KEYS:
                value = payload.get(key)
                if isinstance(value, list):
                    payload = value
                    break
        if not isinstance(payload, list):
            raise _ShapeError("batch response is not a list")
        if len(payload) != len(DECISION_QUESTIONS):
            raise _ShapeError(
                f"batch response has {len(payload)} items, expected {len(DECISION_QUESTIONS)}"
            )
        answers: dict[str, tuple[str, dict[str, float]]] = {}
        for spec, item in zip(DECISION_QUESTIONS, payload):
            answer, probabilities, _route = _extract_decision(item)
            answers[spec.name] = (answer, probabilities)
        return answers

    def _decide_inprocess(self, state: str) -> dict[str, tuple[str, dict[str, float]]]:
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
            for spec in DECISION_QUESTIONS
        ]
        results = decider.decide(state, questions)
        answers: dict[str, tuple[str, dict[str, float]]] = {}
        for spec in DECISION_QUESTIONS:
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

    # -- assembly ----------------------------------------------------------

    def _assemble(
        self,
        answers: dict[str, tuple[str, dict[str, float]]],
        *,
        latency_ms: float,
        route: str,
    ) -> RawDecision:
        action, action_probs = answers["action"]
        intent, _intent_probs = answers["intent"]
        emotion, _emotion_probs = answers["emotion"]
        clarify, _clarify_probs = answers["clarify"]
        complex_review, _complex_probs = answers.get("complex_review", ("no", {}))

        confidence = float(action_probs.get(action, 0.0))
        return RawDecision(
            provider=self.name,
            action=str(action).strip().lower(),
            scale_name=None,
            emotion=str(emotion).strip().lower(),
            intensity=0.0,
            needs_rag=str(action).strip().lower() == "chat",
            confidence=confidence,
            reason=f"anyjev:{route}",
            intent_jev=str(intent).strip().lower(),
            intent_legacy=str(intent).strip().lower(),
            clarify=str(clarify).strip().lower() == "yes",
            needs_complex_review=str(complex_review).strip().lower() == "yes",
            probabilities=dict(action_probs),
            per_question={name: dict(probs) for name, (_answer, probs) in answers.items()},
            latency_ms=latency_ms,
            route=route,
        )
