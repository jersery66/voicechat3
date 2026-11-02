"""Deterministic fake provider for offline tests and benchmarks.

No GPU, no network, no model.  It exists so the whole contract — provider →
validator → coordinator → AgentObservation → TurnPolicy — is testable without a
decision checkpoint, which is a hard requirement while no Tacit weights are
installed.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Optional, Sequence

from decision.contracts import DecisionRequest, RawDecision


def make_decision(
    action: str,
    *,
    probs: Optional[dict[str, float]] = None,
    confidence: Optional[float] = None,
    emotion: str = "calm",
    intent: str = "chat",
    scale_name: Optional[str] = None,
    clarify: bool = False,
    needs_complex_review: bool = False,
    provider: str = "fake",
) -> RawDecision:
    """Build one synthetic decision with a well-formed distribution."""
    distribution = dict(probs or {})
    if not distribution:
        remainder = 0.2
        distribution = {action: 1.0 - remainder}
        distribution["chat" if action != "chat" else "end_session"] = remainder
    if confidence is None:
        confidence = distribution.get(action, 0.0)
    return RawDecision(
        provider=provider,
        action=action,
        scale_name=scale_name,
        emotion=emotion,
        intensity=0.4,
        needs_rag=action == "chat",
        confidence=confidence,
        reason="fake",
        intent_jev=intent,
        intent_legacy=intent,
        clarify=clarify,
        needs_complex_review=needs_complex_review,
        probabilities=distribution,
        per_question={"action": distribution},
    )


def make_intervention_decision(
    intervention: str,
    *,
    offer_target: Optional[str] = None,
    probs: Optional[dict[str, float]] = None,
    confidence: Optional[float] = None,
    emotion: str = "calm",
    intent: str = "chat",
    needs_complex_review: bool = False,
    provider: str = "fake",
    reason: str = "fake_intervention",
) -> RawDecision:
    """Build one synthetic four-way timing decision with a valid distribution."""
    from decision.timing import INTERVENTION_LABELS

    distribution = dict(probs or {})
    if not distribution:
        others = [label for label in INTERVENTION_LABELS if label != intervention]
        if confidence is None:
            share = 0.12
            top = 1.0 - share * len(others)
        else:
            top = max(0.0, min(1.0, float(confidence)))
            share = (1.0 - top) / len(others) if others else 0.0
        distribution = {intervention: top}
        for label in others:
            distribution[label] = share
    if confidence is None:
        confidence = distribution.get(intervention, 0.0)
    return RawDecision(
        provider=provider,
        intervention=intervention,
        offer_target=offer_target,
        scale_name=offer_target,
        emotion=emotion,
        intensity=0.4,
        needs_rag=intervention == "continue_interview",
        confidence=confidence,
        reason=reason,
        intent_jev=intent,
        intent_legacy=intent,
        clarify=intervention == "clarify_first",
        needs_complex_review=needs_complex_review,
        probabilities=distribution,
        per_question={"intervention": distribution},
    )


class FakeDecisionProvider:
    """Replay a script of decisions, or compute them with a callable."""

    name = "fake"

    def __init__(
        self,
        script: Optional[Iterable[Any]] = None,
        *,
        handler: Optional[Callable[[DecisionRequest], RawDecision]] = None,
        latency_ms: float = 0.0,
        error: Optional[BaseException] = None,
        available: bool = True,
    ) -> None:
        self._script: list[Any] = list(script or [])
        self._handler = handler
        self._latency_ms = max(0.0, float(latency_ms))
        self._error = error
        self._available = bool(available)
        self.calls: list[DecisionRequest] = []

    # -- provider protocol -------------------------------------------------

    def is_available(self) -> bool:
        return self._available

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "available": self._available,
            "scripted": len(self._script),
            "latency_ms": self._latency_ms,
        }

    def decide(self, request: DecisionRequest) -> RawDecision:
        self.calls.append(request)
        if self._latency_ms:
            time.sleep(self._latency_ms / 1000.0)
        if self._error is not None:
            raise self._error
        if self._handler is not None:
            return self._handler(request)
        if not self._script:
            return make_decision("chat", probs={"chat": 0.9, "end_session": 0.1})
        item = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        if isinstance(item, RawDecision):
            return item
        if isinstance(item, dict):
            return make_decision(**item)
        if isinstance(item, BaseException):
            raise item
        raise TypeError(f"unsupported script entry: {type(item).__name__}")

    # -- helpers -----------------------------------------------------------

    def queue(self, *items: Any) -> "FakeDecisionProvider":
        self._script.extend(items)
        return self

    @property
    def remaining(self) -> int:
        return len(self._script)


def constant_provider(action: str, confidence: float = 0.9, **kwargs: Any) -> FakeDecisionProvider:
    """A provider that always answers the same label."""

    def handler(_request: DecisionRequest) -> RawDecision:
        probs = {action: confidence}
        other = "chat" if action != "chat" else "end_session"
        probs[other] = max(0.0, 1.0 - confidence)
        return make_decision(action, probs=probs, **kwargs)

    return FakeDecisionProvider(handler=handler)


def sequence_provider(actions: Sequence[str], confidence: float = 0.9) -> FakeDecisionProvider:
    """One provider that walks through ``actions`` in order, then repeats the last."""
    script = [make_decision(action, confidence=confidence) for action in actions]
    return FakeDecisionProvider(script=script)


def intervention_provider(
    intervention: str,
    confidence: float = 0.9,
    *,
    offer_target: Optional[str] = None,
) -> FakeDecisionProvider:
    """A provider that always answers the same timing decision."""

    def handler(request: DecisionRequest) -> RawDecision:
        target = offer_target
        if target is None and intervention == "offer_scale":
            candidates = request.scale_candidates()
            target = candidates[0].name if candidates else None
        return make_intervention_decision(
            intervention, offer_target=target, confidence=confidence
        )

    return FakeDecisionProvider(handler=handler)


def timing_script_provider(items: Sequence[str], confidence: float = 0.9) -> FakeDecisionProvider:
    """Walk through a list of intervention labels, then repeat the last one."""
    script = [make_intervention_decision(item, confidence=confidence) for item in items]
    return FakeDecisionProvider(script=script)
