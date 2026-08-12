"""Streaming dialogue model contract."""

from typing import Iterator, Protocol, runtime_checkable


@runtime_checkable
class DialogueClient(Protocol):
    def stream_reply(self, *, user_text: str, system_context: str = "") -> Iterator[str]: ...
