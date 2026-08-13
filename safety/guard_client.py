"""Legacy model-guard contract, isolated from production inference adapters."""

from typing import Protocol, runtime_checkable

from safety.types import SafetyDecision


@runtime_checkable
class GuardClient(Protocol):
    """Contract for the optional, explicitly invoked legacy Guard client."""

    def assess_input(self, text: str) -> SafetyDecision: ...


__all__ = ["GuardClient"]
