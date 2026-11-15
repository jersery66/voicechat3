"""Adapter that keeps the existing Qwen Agent exactly as it is today.

This provider exists so that ``legacy`` mode is not a re-implementation of the
RC1 behaviour but literally the same call.  It preserves the three historical
call shapes the pipeline accepted (``observe_turn`` / ``route_proposal`` /
``route_conversation_actions``) and returns the produced ``AgentObservation``
verbatim in ``RawDecision.observation``.

Consequence: nothing in legacy mode is validated, re-scored, or translated, so
existing tests and production behaviour are unchanged by construction.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from conversation.agent_observation import AgentObservation
from conversation.contracts import RouterAction, RouterProposal
from decision.contracts import (
    DecisionRequest,
    RawDecision,
    map_intent_to_legacy,
)
from decision.providers.base import DecisionProviderError


class LegacyDecisionProvider:
    """Wrap ``services.agent_service.AgentService`` (or a compatible double)."""

    name = "legacy_qwen"

    def __init__(self, agent: Any, *, timeout: Optional[float] = None) -> None:
        self.agent = agent
        self.timeout = timeout

    # -- provider protocol -------------------------------------------------

    def is_available(self) -> bool:
        if self.agent is None:
            return False
        checker = getattr(self.agent, "is_available", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        return True

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": getattr(self.agent, "model", None),
            "available": self.is_available(),
        }

    def decide(self, request: DecisionRequest) -> RawDecision:
        started = time.perf_counter()
        observation = self.observe(request)
        latency_ms = (time.perf_counter() - started) * 1000.0
        proposal = observation.proposal
        return RawDecision(
            provider=self.name,
            observation=observation,
            action=proposal.action.value,
            scale_name=proposal.scale_name,
            emotion=proposal.emotion,
            intensity=proposal.intensity,
            needs_rag=proposal.needs_rag,
            confidence=proposal.confidence,
            reason=proposal.reason,
            intent_legacy=observation.intent,
            intent_jev=None,
            latency_ms=latency_ms,
            fallback_used=observation.fallback_used,
        )

    # -- the historical call, unchanged ------------------------------------

    def observe(self, request: DecisionRequest) -> AgentObservation:
        """Reproduce the pre-experiment pipeline call exactly."""
        if self.agent is None:
            raise DecisionProviderError("legacy agent is not configured")
        kwargs = request.legacy_kwargs()
        if self.timeout is not None:
            kwargs["timeout"] = self.timeout

        if hasattr(self.agent, "observe_turn"):
            raw_observation = self.agent.observe_turn(**kwargs)
            if isinstance(raw_observation, AgentObservation):
                return raw_observation
            raw = dict(raw_observation or {})
            proposal = raw.get("proposal")
            if not isinstance(proposal, RouterProposal):
                proposal = RouterProposal.from_legacy_route(raw)
            return AgentObservation(
                proposal=proposal,
                intent=str(raw.get("intent", "counseling") or "counseling"),
                fallback_used=bool(raw.get("fallback_used", False)),
                source=str(raw.get("source", "agent") or "agent"),
            )
        if hasattr(self.agent, "route_proposal"):
            raw = self.agent.route_proposal(**kwargs)
            proposal = raw if isinstance(raw, RouterProposal) else RouterProposal.from_legacy_route(raw)
            return AgentObservation(proposal=proposal)
        if hasattr(self.agent, "route_conversation_actions"):
            raw = self.agent.route_conversation_actions(**kwargs)
            proposal = raw if isinstance(raw, RouterProposal) else RouterProposal.from_legacy_route(raw)
            return AgentObservation(
                proposal=proposal,
                intent=(
                    str(raw.get("intent", "counseling") or "counseling")
                    if isinstance(raw, dict)
                    else "counseling"
                ),
                fallback_used=bool(raw.get("fallback_used", False)) if isinstance(raw, dict) else False,
            )
        raise DecisionProviderError(
            "legacy agent exposes none of observe_turn/route_proposal/route_conversation_actions"
        )


def deterministic_observation(
    agent: Any,
    user_text: str,
    *,
    reason: str = "agent_observation_fallback",
) -> AgentObservation:
    """Local keyword-only observation, used when no provider can answer.

    Mirrors ``ConversationPipeline._agent_deterministic_fallback`` so that both
    paths keep producing the same neutral CHAT observation.
    """
    intent = "counseling"
    emotion = "neutral"
    intensity = 0.0
    if agent is not None:
        keyword_intent = getattr(agent, "_keyword_classify", None)
        keyword_emotion = getattr(agent, "_keyword_detect_emotion", None)
        if callable(keyword_intent):
            try:
                intent = str(keyword_intent(user_text).get("intent", intent))
            except Exception:
                pass
        if callable(keyword_emotion):
            try:
                observed = keyword_emotion(user_text)
                emotion = str(observed.get("emotion", emotion))
                intensity = float(observed.get("intensity", intensity) or intensity)
            except Exception:
                pass
    proposal = RouterProposal(
        action=RouterAction.CHAT,
        emotion=emotion,
        intensity=max(0.0, min(1.0, intensity)),
        confidence=0.0,
        reason=reason,
        needs_rag=False,
    )
    return AgentObservation(
        proposal=proposal,
        intent=map_intent_to_legacy(intent) if intent in {"chitchat", "relaxation", "entertainment", "counseling"} else intent,
        fallback_used=True,
        source="deterministic_fallback",
    )
