"""Immutable Phase A contracts for future support activities."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, ClassVar, Mapping

from pydantic import ConfigDict, Field, field_validator
from pydantic import BaseModel


class ActivityRuntimeError(RuntimeError):
    """Deterministic failure at the ActivityRuntime boundary."""


class ActivityStatus(str, Enum):
    INACTIVE = "INACTIVE"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class FrozenDict(dict):
    """Small read-only mapping used by snapshots and results."""

    def _blocked(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("mapping is read-only")

    __setitem__ = _blocked
    __delitem__ = _blocked
    clear = _blocked
    pop = _blocked
    popitem = _blocked
    setdefault = _blocked
    update = _blocked


_STAGES = {"RAPPORT", "EXPLORATION", "ASSESSMENT", "STABILIZATION", "RECOVERY"}
_LOADS = {"LOW", "MODERATE", "HIGH"}
_NEEDS = {
    "immediate_stabilization",
    "craving_coping",
    "trigger_awareness",
    "refusal_skill",
    "coping_skill",
    "change_motivation",
    "recovery_planning",
}


class _FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)


class ActivityDefinition(_FrozenContract):
    """Catalog-owned, non-executable activity configuration."""

    _ID_RE: ClassVar[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]*$")

    id: str
    display_name: str
    category: str
    target_need: str
    min_need_score: float = Field(ge=0.0, le=1.0)
    min_activity_readiness: float = Field(ge=0.0, le=1.0)
    allowed_user_load: tuple[str, ...]
    allowed_conversation_stages: tuple[str, ...]
    opt_in_required: bool = True
    proactive_allowed: bool = True
    max_per_session: int = Field(default=1, ge=1)
    cooldown_rounds: int = Field(default=0, ge=0)
    expected_duration_minutes: float = Field(default=5.0, ge=0.0)
    can_interrupt_scale: bool = False
    resume_scale_after: bool = True
    uses_media: bool = False
    requires_voice_input: bool = False
    result_schema: str
    evidence_status: str

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not cls._ID_RE.fullmatch(value):
            raise ValueError("id must be stable snake_case")
        return value

    @field_validator("display_name", "category", "result_schema", "evidence_status")
    @classmethod
    def _validate_non_empty(cls, value: str) -> str:
        if not str(value).strip():
            raise ValueError("value must be non-empty")
        return str(value).strip()

    @field_validator("target_need")
    @classmethod
    def _validate_need(cls, value: str) -> str:
        if value not in _NEEDS:
            raise ValueError(f"unknown target_need: {value!r}")
        return value

    @field_validator("allowed_user_load")
    @classmethod
    def _validate_loads(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or any(value not in _LOADS for value in values):
            raise ValueError("allowed_user_load contains an unknown value")
        return tuple(dict.fromkeys(values))

    @field_validator("allowed_conversation_stages")
    @classmethod
    def _validate_stages(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or any(value not in _STAGES for value in values):
            raise ValueError("allowed_conversation_stages contains an unknown value")
        return tuple(dict.fromkeys(values))

    @field_validator("opt_in_required")
    @classmethod
    def _require_opt_in(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("all visible activities require opt-in")
        return value


class ActivityCandidate(_FrozenContract):
    """A ranked suggestion; it never starts an activity."""

    activity_id: str
    score: float = Field(ge=0.0, le=1.0)
    target_need: str
    reason: str = ""
    source: str = "agent_observation"

    @field_validator("target_need")
    @classmethod
    def _validate_need(cls, value: str) -> str:
        if value not in _NEEDS:
            raise ValueError(f"unknown target_need: {value!r}")
        return value


def _frozen_mapping(value: Mapping[str, Any] | None) -> FrozenDict:
    if value is None:
        return FrozenDict()
    if not isinstance(value, Mapping):
        raise TypeError("responses must be a mapping")
    return FrozenDict(dict(value))


class ActivityResult(_FrozenContract):
    """Committed activity facts; not a clinical score."""

    activity_session_id: str
    activity_id: str
    completion_status: ActivityStatus
    responses: FrozenDict = Field(default_factory=FrozenDict)
    started_at: str | None = None
    completed_at: str | None = None
    cancelled_at: str | None = None
    cancel_reason: str | None = None
    pre_rating: float | None = Field(default=None, ge=0.0, le=10.0)
    post_rating: float | None = Field(default=None, ge=0.0, le=10.0)

    @field_validator("responses", mode="before")
    @classmethod
    def _freeze_responses(cls, value: Mapping[str, Any] | None) -> FrozenDict:
        return _frozen_mapping(value)


class ActivityRuntimeSnapshot(_FrozenContract):
    """Read-only view of the activity writer's current state."""

    activity_session_id: str | None = None
    active_activity: str | None = None
    activity_category: str | None = None
    status: ActivityStatus = ActivityStatus.INACTIVE
    current_step: int | None = Field(default=None, ge=1)
    responses: FrozenDict = Field(default_factory=FrozenDict)
    started_at: str | None = None
    completed_at: str | None = None
    completed: bool = False
    cancelled: bool = False
    cancel_reason: str | None = None
    paused: bool = False
    pre_rating: float | None = Field(default=None, ge=0.0, le=10.0)
    post_rating: float | None = Field(default=None, ge=0.0, le=10.0)
    metadata: FrozenDict = Field(default_factory=FrozenDict)

    @field_validator("responses", "metadata", mode="before")
    @classmethod
    def _freeze_mapping(cls, value: Mapping[str, Any] | None) -> FrozenDict:
        return _frozen_mapping(value)
