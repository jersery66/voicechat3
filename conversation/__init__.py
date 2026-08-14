"""Conversation orchestration above providers and below UI transports."""

from conversation.contracts import (
    RouterAction,
    RouterProposal,
    TurnAction,
    TurnDecision,
    TurnStateSnapshot,
)
__all__ = [
    "ConversationCoordinator",
    "ConversationContextBuilder",
    "ResponseBuilder",
    "RouterAction",
    "RouterProposal",
    "TurnAction",
    "TurnDecision",
    "TurnStateSnapshot",
]


def __getattr__(name):
    """Keep package-level compatibility without importing the pipeline early."""
    if name == "ConversationCoordinator":
        from conversation.coordinator import ConversationCoordinator

        return ConversationCoordinator
    if name == "ConversationContextBuilder":
        from conversation.context_builder import ConversationContextBuilder

        return ConversationContextBuilder
    if name == "ResponseBuilder":
        from conversation.response_builder import ResponseBuilder

        return ResponseBuilder
    raise AttributeError(name)
