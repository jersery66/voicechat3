"""Tacit checkpoint adapter (Tacit-1.7B / 2B / 4B).

Kept separate from :mod:`decision.providers.anyjev` on purpose.  The two
backends look similar but are not the same API:

- ``Decider`` reads K-option choices from a general causal LM and carries a
  ``level`` (raw / L0) plus rotation settings.
- ``Tacit`` is a self-distilled checkpoint: one prefill per decision, it
  returns ``route`` (``"one_forward"`` or ``"cot"``) and can escalate a capped
  share of decisions to its own reasoning.

Collapsing them into one adapter would have to ignore exactly the fields that
make each backend verifiable, so they stay separate and share only the socket
transport.

The in-process path is implemented but unexecuted in this repository: no Tacit
checkpoint is installed, and nothing here downloads weights implicitly.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Optional

from decision.contracts import DecisionRequest, RawDecision
from decision.providers.base import (
    DECISION_QUESTIONS,
    DecisionBackendUnavailable,
    build_state_text,
)
from decision.providers.http_transport import HttpDecisionTransport

_ROUTE_VALUES = ("one_forward", "cot", "batch", "sequential", "inprocess")


def _parse_tacit_payload(payload: Any) -> tuple[str, dict[str, float], Optional[str]]:
    """Read one Tacit decision: ``{"answer", "probs", "route"}``."""
    if not isinstance(payload, dict):
        raise DecisionBackendUnavailable(
            f"tacit gateway returned {type(payload).__name__}, expected an object"
        )
    answer = payload.get("answer") or payload.get("label")
    probabilities = payload.get("probs") or payload.get("probabilities")
    if not isinstance(answer, str) or not isinstance(probabilities, dict) or not probabilities:
        raise DecisionBackendUnavailable(
            "tacit gateway response has no usable answer/probs; "
            f"keys={sorted(payload)}"
        )
    route = payload.get("route")
    return answer.strip(), {str(k): float(v) for k, v in probabilities.items()}, (
        str(route) if route else None
    )


class TacitDecisionProvider:
    """Tacit decision backend, over a local gateway or in-process."""

    name = "tacit"

    def __init__(
        self,
        *,
        transport: str = "server",
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 5.0,
        allow_remote_endpoint: bool = False,
        allow_model_download: bool = False,
        adaptive: bool = False,
        tau: float = 0.5,
        max_cot_share: Optional[float] = 0.2,
        tacit_factory: Optional[Any] = None,
    ) -> None:
        self.transport = str(transport or "server").strip().lower()
        if self.transport not in {"server", "inprocess"}:
            raise ValueError("tacit transport must be 'server' or 'inprocess'")
        self.endpoint = endpoint
        self.model = model
        self.timeout = float(timeout)
        self.allow_remote_endpoint = bool(allow_remote_endpoint)
        self.allow_model_download = bool(allow_model_download)
        self.adaptive = bool(adaptive)
        self.tau = float(tau)
        self.max_cot_share = max_cot_share
        self._tacit = None
        self._tacit_factory = tacit_factory

        self._http: Optional[HttpDecisionTransport] = None
        if self.transport == "server":
            if not endpoint:
                raise DecisionBackendUnavailable(
                    "tacit server transport needs decision.endpoint "
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
            return self._ensure_tacit() is not None
        except DecisionBackendUnavailable:
            return False

    def describe(self) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "provider": self.name,
            "transport": self.transport,
            "model": self.model,
            "adaptive": self.adaptive,
            "tau": self.tau,
        }
        if self._http is not None:
            detail.update(self._http.describe())
        if self._tacit is not None:
            stats = getattr(self._tacit, "stats", None)
            if stats is not None:
                detail["stats"] = stats
        return detail

    def decide(self, request: DecisionRequest) -> RawDecision:
        state = build_state_text(request)
        started = time.perf_counter()
        if self.transport == "server":
            answers, routes = self._decide_over_http(state)
        else:
            answers, routes = self._decide_inprocess(state)
        latency_ms = (time.perf_counter() - started) * 1000.0

        action, action_probs = answers["action"]
        intent, _ = answers["intent"]
        emotion, _ = answers["emotion"]
        clarify, _ = answers["clarify"]
        complex_review, _ = answers.get("complex_review", ("no", {}))
        escalated = [value for value in routes if value == "cot"]

        return RawDecision(
            provider=self.name,
            action=action.strip().lower(),
            scale_name=None,
            emotion=emotion.strip().lower(),
            intensity=0.0,
            needs_rag=action.strip().lower() == "chat",
            confidence=float(action_probs.get(action, 0.0)),
            reason="tacit" + ("+cot" if escalated else ""),
            intent_jev=intent.strip().lower(),
            intent_legacy=intent.strip().lower(),
            clarify=clarify.strip().lower() == "yes",
            needs_complex_review=complex_review.strip().lower() == "yes",
            probabilities=dict(action_probs),
            per_question={name: dict(probs) for name, (_a, probs) in answers.items()},
            latency_ms=latency_ms,
            route="cot" if escalated else "one_forward",
        )

    # -- transports --------------------------------------------------------

    def _decide_over_http(
        self, state: str
    ) -> tuple[dict[str, tuple[str, dict[str, float]]], list[str]]:
        assert self._http is not None
        answers: dict[str, tuple[str, dict[str, float]]] = {}
        routes: list[str] = []
        for spec in DECISION_QUESTIONS:
            payload = self._http.post({"state": state, **spec.payload()})
            answer, probabilities, route = _parse_tacit_payload(payload)
            answers[spec.name] = (answer, probabilities)
            routes.append(route or "one_forward")
        return answers, routes

    def _decide_inprocess(
        self, state: str
    ) -> tuple[dict[str, tuple[str, dict[str, float]]], list[str]]:
        tacit = self._ensure_tacit()
        if tacit is None:
            raise DecisionBackendUnavailable("tacit checkpoint is not available")
        # One `decide_batch` call, not five: official Tacit batches the questions
        # of one turn into a single forward, and that is the latency we quote.
        batch = [
            {
                "state": state,
                "question": spec.text,
                "options": list(spec.options),
                "kind": "choice",
            }
            for spec in DECISION_QUESTIONS
        ]
        try:
            results = tacit.decide_batch(batch)
        except Exception as exc:  # a broken engine must not look like a decision
            raise DecisionBackendUnavailable(
                f"tacit decide_batch failed: {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(results, list) or len(results) != len(batch):
            raise DecisionBackendUnavailable(
                f"tacit returned {len(results) if isinstance(results, list) else '?'} "
                f"decisions for {len(batch)} questions"
            )

        answers: dict[str, tuple[str, dict[str, float]]] = {}
        routes: list[str] = []
        for spec, result in zip(DECISION_QUESTIONS, results):
            if not isinstance(result, dict):
                raise DecisionBackendUnavailable(
                    f"tacit returned {type(result).__name__} for {spec.name!r}"
                )
            answer = result.get("answer")
            probabilities = result.get("probs")
            if not answer or not isinstance(probabilities, dict) or not probabilities:
                raise DecisionBackendUnavailable(
                    f"tacit returned an unusable decision for {spec.name!r}"
                )
            answers[spec.name] = (str(answer), {str(k): float(v) for k, v in probabilities.items()})
            routes.append(str(result.get("route") or "one_forward"))
        return answers, routes

    def _ensure_tacit(self):
        if self._tacit is not None:
            return self._tacit
        if self._tacit_factory is not None:
            self._tacit = self._tacit_factory(self)
            return self._tacit
        if not self.model:
            return None

        candidate = Path(self.model).expanduser()
        is_directory = candidate.is_dir()
        if not is_directory:
            if not self.allow_model_download:
                raise DecisionBackendUnavailable(
                    f"decision.model {self.model!r} is not a local directory; "
                    "set decision.allow_model_download to true only if a hub fetch is acceptable, "
                    "otherwise point decision.model_path at a verified checkpoint"
                )

        # Offline is enforced before the import, not after: `Tacit.from_pretrained`
        # calls AutoTokenizer/AutoModelForCausalLM without local_files_only, so the
        # environment is the only thing that stops a repo id from reaching the hub.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        try:
            from anyjev import Tacit
        except Exception as exc:  # pragma: no cover - optional dependency
            raise DecisionBackendUnavailable(
                f"anyjev[hf] is not installed: {exc}. Install it in an isolated env; "
                "do not add it to the project .venv."
            ) from exc
        try:
            self._tacit = Tacit.from_pretrained(
                str(candidate) if is_directory else self.model,
                adaptive=self.adaptive,
                tau=self.tau,
                max_cot_share=self.max_cot_share,
            )
        except Exception as exc:
            raise DecisionBackendUnavailable(
                f"tacit checkpoint failed to load: {type(exc).__name__}: {exc}"
            ) from exc
        return self._tacit

