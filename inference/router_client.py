"""Structured routing-model contract."""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RouterClient(Protocol):
    def route(self, *, user_text: str, recent_history: str = "") -> dict[str, Any]: ...
