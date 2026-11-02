"""Run one decision turn in legacy, shadow, or jev mode.

Invariants this coordinator guarantees:

1. ``legacy`` mode is a pure pass-through.  It does not validate, re-score, or
   translate; the observation that leaves this class is the object the old
   pipeline produced, so RC1 behaviour is preserved by construction.
2. ``shadow`` mode computes a second opinion and records the comparison, but
   only the legacy observation is returned.  The shadow provider cannot reach
   the session, the scale runtime, or the emotion history — it is a pure
   function of the immutable ``DecisionRequest``.
3. ``jev`` mode forwards a *validated* proposal.  Anything malformed,
   contradictory, low-confidence, or unsafe falls back per policy.  A model can
   therefore never start a scale, end an interview, or launch an activity on
   its own authority; that remains ``TurnPolicy``'s job.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from conversation.agent_observation import AgentObservation
from decision.contracts import (
    DecisionMode,
    DecisionOutcome,
    DecisionProvider,
    DecisionRequest,
    EscalationMode,
    RawDecision,
    build_observation,
)
from decision.providers.legacy import deterministic_observation
from decision.validator import DecisionValidator

from services.logger import get_logger

logger = get_logger(__name__)


class DecisionCoordinator:
    """Own the mode switch and the fallback policy for one decision turn."""

    def __init__(
        self,
        *,
        mode: DecisionMode,
        primary: DecisionProvider,
        validator: Optional[DecisionValidator] = None,
        shadow: Optional[DecisionProvider] = None,
        legacy: Optional[DecisionProvider] = None,
        escalation: EscalationMode = EscalationMode.LEGACY,
        journal: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        self.mode = DecisionMode(mode)
        self.primary = primary
        self.validator = validator or DecisionValidator(require_probabilities=(self.mode is DecisionMode.JEV))
        self.shadow = shadow
        self.legacy = legacy
        self.escalation = EscalationMode(escalation)
        self.journal = journal

        if self.mode is DecisionMode.SHADOW and self.shadow is None:
            raise ValueError("shadow mode requires a shadow provider")
        if self.mode is DecisionMode.JEV and self.primary is self.legacy:
            raise ValueError("jev mode requires a decision backend that is not the legacy agent")

    # -- public API --------------------------------------------------------

    def observe_turn(self, request: DecisionRequest) -> DecisionOutcome:
        if self.mode is DecisionMode.LEGACY:
            return self._legacy_turn(request)
        if self.mode is DecisionMode.SHADOW:
            return self._shadow_turn(request)
        return self._jev_turn(request)

    def describe(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "primary": self.primary.describe() if hasattr(self.primary, "describe") else self.primary.name,
            "shadow": (
                self.shadow.describe() if self.shadow is not None and hasattr(self.shadow, "describe") else None
            ),
            "escalation": self.escalation.value,
            "validator": self.validator.describe(),
        }

    # -- modes -------------------------------------------------------------

    def _legacy_turn(self, request: DecisionRequest) -> DecisionOutcome:
        started = time.perf_counter()
        decision = self.primary.decide(request)
        observation = build_observation(decision, source="agent", fallback_used=decision.fallback_used)
        outcome = DecisionOutcome(
            observation=observation,
            mode=self.mode,
            provider=decision.provider,
            decision=decision,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
        self._record(outcome, request)
        return outcome

    def _shadow_turn(self, request: DecisionRequest) -> DecisionOutcome:
        started = time.perf_counter()
        authoritative = self.primary.decide(request)
        observation = build_observation(
            authoritative, source="agent", fallback_used=authoritative.fallback_used
        )

        shadow_decision: Optional[RawDecision] = None
        comparison: dict[str, Any] = {"shadow_ran": False}
        try:
            assert self.shadow is not None
            shadow_decision = self.shadow.decide(request)
            comparison = self._compare(authoritative, shadow_decision)
        except Exception as exc:  # shadow must never break the conversation
            comparison = {
                "shadow_ran": False,
                "shadow_error": type(exc).__name__,
                "shadow_error_detail": str(exc)[:160],
            }

        outcome = DecisionOutcome(
            observation=observation,
            mode=self.mode,
            provider=authoritative.provider,
            decision=authoritative,
            shadow=shadow_decision,
            comparison=comparison,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
        self._record(outcome, request)
        return outcome

    def _jev_turn(self, request: DecisionRequest) -> DecisionOutcome:
        started = time.perf_counter()
        failure_reason: Optional[str] = None
        abstained = False
        candidate: Optional[RawDecision] = None

        try:
            candidate = self.primary.decide(request)
        except Exception as exc:
            failure_reason = "provider_error"
            logger.warning(
                "[Decision] provider failed: provider=%s error=%s",
                self.primary.name,
                type(exc).__name__,
            )

        if candidate is not None:
            verdict = self.validator.validate(candidate, request)
            if verdict.accepted:
                observation = build_observation(
                    verdict.decision, source="jev", fallback_used=False
                )
                outcome = DecisionOutcome(
                    observation=observation,
                    mode=self.mode,
                    provider=verdict.decision.provider,
                    decision=verdict.decision,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                )
                self._record(outcome, request)
                return outcome
            abstained = verdict.abstained
            failure_reason = verdict.reason
            candidate = verdict.decision

        observation, source = self._escalate(request, failure_reason or "unknown")
        outcome = DecisionOutcome(
            observation=observation,
            mode=self.mode,
            provider=(candidate.provider if candidate is not None else self.primary.name),
            decision=candidate or RawDecision(provider=self.primary.name, error=failure_reason),
            abstained=abstained,
            reject_reason=failure_reason,
            escalated=True,
            escalation_source=source,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
        self._record(outcome, request)
        return outcome

    # -- fallback ----------------------------------------------------------

    def _escalate(self, request: DecisionRequest, reason: str) -> tuple[AgentObservation, str]:
        """Return (observation, source) for a rejected or failed Jev decision."""
        if self.escalation is EscalationMode.LEGACY and self.legacy is not None:
            try:
                fallback = self.legacy.decide(request)
                observation = build_observation(
                    fallback, source="agent", fallback_used=fallback.fallback_used
                )
                logger.warning(
                    "[Decision] escalated to legacy: reason=%s", reason
                )
                return observation, "legacy"
            except Exception as exc:
                logger.warning(
                    "[Decision] legacy escalation failed: reason=%s error=%s",
                    reason,
                    type(exc).__name__,
                )
        agent = getattr(self.legacy, "agent", None) if self.legacy is not None else None
        return (
            deterministic_observation(agent, request.user_text, reason=f"decision_fallback:{reason}"),
            "deterministic",
        )

    # -- diagnostics -------------------------------------------------------

    @staticmethod
    def _compare(authoritative: RawDecision, shadow: RawDecision) -> dict[str, Any]:
        authoritative_action = (authoritative.action or "").strip().lower()
        shadow_action = (shadow.action or "").strip().lower()
        return {
            "shadow_ran": True,
            "shadow_provider": shadow.provider,
            "authoritative_action": authoritative_action,
            "shadow_action": shadow_action,
            "action_match": bool(authoritative_action) and authoritative_action == shadow_action,
            "emotion_match": (
                (authoritative.emotion or "").strip().lower()
                == (shadow.emotion or "").strip().lower()
            ),
            "intent_match": (
                (authoritative.intent_jev or authoritative.intent_legacy or "").strip().lower()
                == (shadow.intent_jev or shadow.intent_legacy or "").strip().lower()
            ),
            "confidence_delta": round(float(shadow.confidence) - float(authoritative.confidence), 4),
            "latency_delta_ms": round(float(shadow.latency_ms) - float(authoritative.latency_ms), 2),
            "shadow_route": shadow.route,
        }

    def _record(self, outcome: DecisionOutcome, request: DecisionRequest) -> None:
        payload = {
            "mode": outcome.mode.value,
            "provider": outcome.provider,
            "action": outcome.decision.action,
            "emotion": outcome.decision.emotion,
            "intent": outcome.decision.intent_jev or outcome.decision.intent_legacy,
            "confidence": round(float(outcome.decision.confidence or 0.0), 4),
            "latency_ms": round(float(outcome.latency_ms or 0.0), 2),
            "abstained": outcome.abstained,
            "reject_reason": outcome.reject_reason,
            "escalated": outcome.escalated,
            "escalation_source": outcome.escalation_source,
            "route": outcome.decision.route,
            "comparison": outcome.comparison or None,
            "request": request.redacted(),
        }
        # Metadata only.  No utterance, no history, no model output.
        logger.warning(
            "[Decision] mode=%s provider=%s action=%s confidence=%.3f escalated=%s reason=%s",
            payload["mode"],
            payload["provider"],
            payload["action"],
            payload["confidence"],
            payload["escalated"],
            payload["reject_reason"],
        )
        if self.journal is not None:
            try:
                self.journal(payload)
            except Exception as exc:  # journaling must never break a turn
                logger.warning("[Decision] journal write failed: %s", type(exc).__name__)
