"""Immutable observation produced by one ordinary-turn Agent request."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from conversation.contracts import RouterProposal, _FrozenContract


class AgentObservation(_FrozenContract):
    """Non-executable Agent facts for one user turn.

    ``proposal`` is the only part that reaches TurnPolicy. ``intent`` and the
    proposal's emotion/intensity are observation consumers only.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")
    proposal: RouterProposal
    intent: str = Field(default="counseling")
    fallback_used: bool = False
    source: str = "agent"
