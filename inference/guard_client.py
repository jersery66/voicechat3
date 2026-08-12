"""Model safety guard contract used as an optional second opinion."""

from typing import Protocol, runtime_checkable

from safety.types import SafetyDecision


@runtime_checkable
class GuardClient(Protocol):
    def assess_input(self, text: str) -> SafetyDecision: ...
