# Tools - Lightweight tool pattern for encapsulating side-effectful operations

from typing import Protocol, Any


class Tool(Protocol):
    """Lightweight tool interface. Tools encapsulate side-effectful operations."""
    name: str
    description: str

    def execute(self, **kwargs) -> Any: ...
