"""Typed, auditable decisions emitted by the safety boundary."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class SafetyAction(str, Enum):
    NONE = "none"
    MONITOR = "monitor"
    ESCALATE = "escalate"
    EMERGENCY = "emergency"


class EvidenceSpan(BaseModel):
    """A minimal non-identifying policy evidence record."""

    category: str
    text: str


class SafetyDecision(BaseModel):
    """Policy output, not a diagnosis or a clinical risk assessment."""

    current_suicidal_ideation: bool = False
    self_harm_signal: bool = False
    violence_signal: bool = False
    intent: bool = False
    plan: bool = False
    means: bool = False
    immediacy: bool = False
    historical_signal: bool = False
    protective_signal: bool = False
    uncertainty: bool = False
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    action: SafetyAction = SafetyAction.NONE
    risk_level: int = Field(default=0, ge=0, le=10)
    source: Literal["deterministic", "guard_model", "merged"] = "deterministic"
