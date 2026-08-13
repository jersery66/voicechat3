"""Safety boundary independent of any dialogue, router, or UI provider."""

from __future__ import annotations

from safety.crisis_policy import CrisisPolicy
from safety.guard_client import GuardClient
from safety.types import SafetyDecision


class SafetyGate:
    """Runs deterministic policy first, then admits an optional guard client."""

    def __init__(self, policy: CrisisPolicy | None = None, guard_client: GuardClient | None = None):
        self._policy = policy or CrisisPolicy()
        self._guard_client = guard_client

    def assess_input(self, text: str) -> SafetyDecision:
        deterministic = self._policy.evaluate(text)
        if self._guard_client is None:
            return deterministic
        model_decision = self._guard_client.assess_input(text)
        if model_decision.risk_level > deterministic.risk_level:
            return model_decision.model_copy(update={"source": "merged"})
        return deterministic
