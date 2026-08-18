"""Immutable Relaxation Center V1 contracts."""

from __future__ import annotations

import re
from enum import Enum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RelaxationRuntimeError(RuntimeError):
    """Invalid transition or unavailable content at the relaxation boundary."""


class RelaxationState(str, Enum):
    INACTIVE = "INACTIVE"
    CENTER = "CENTER"
    RUNNING = "RUNNING"
    RETURNING = "RETURNING"


class RelaxationContentType(str, Enum):
    EXERCISE = "EXERCISE"
    VIDEO = "VIDEO"
    GAME = "GAME"


class _FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RelaxationContentDefinition(_FrozenContract):
    """Catalog metadata; it never recommends or starts content."""

    _ID_RE: ClassVar[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]*$")

    id: str
    display_name: str
    category: RelaxationContentType
    enabled: bool = True
    recommended_duration_seconds: int = Field(ge=0)
    max_duration_seconds: int = Field(ge=0)
    requires_mouse: bool = False
    requires_audio: bool = False
    requires_video: bool = False
    resource_path: str | None = None
    implementation_type: str
    sort_order: int = Field(default=0, ge=0)
    implementation_status: str = "PLANNED"

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not cls._ID_RE.fullmatch(value):
            raise ValueError("id must be stable snake_case")
        return value

    @field_validator("display_name", "implementation_type", "implementation_status")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        if not str(value).strip():
            raise ValueError("value must be non-empty")
        return str(value).strip()

    def model_post_init(self, __context) -> None:
        if self.max_duration_seconds < self.recommended_duration_seconds:
            raise ValueError("max_duration_seconds must be >= recommended_duration_seconds")

    @property
    def is_available(self) -> bool:
        return self.enabled and self.implementation_status == "AVAILABLE"


class RelaxationSnapshot(_FrozenContract):
    """Read-only view of the relaxation-center lifecycle."""

    state: RelaxationState = RelaxationState.INACTIVE
    relaxation_session_id: str | None = None
    selected_content_id: str | None = None
    content_type: RelaxationContentType | None = None
    started_at: str | None = None
    ended_at: str | None = None
    completed: bool = False
    cancelled: bool = False
    cancel_reason: str | None = None
