"""Typed policy decisions translated from the legacy agent routing payload."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DialogueAction(str, Enum):
    CHAT = "chat"
    START_SCALE = "start_scale"
    CONTINUE_SCALE = "continue_scale"
    RECOMMEND_RELAXATION = "recommend_relaxation"
    RECOMMEND_GAME = "recommend_game"
    EXIT = "exit"


class ScaleAction(str, Enum):
    NONE = "none"
    START = "start"
    CONTINUE = "continue"
    PAUSE = "pause"


class PolicyDecision(BaseModel):
    """Structured policy output; the coordinator owns its translation."""

    action: DialogueAction = DialogueAction.CHAT
    scale_action: ScaleAction = ScaleAction.NONE
    scale_name: Optional[str] = None
    scale_item: Optional[int] = Field(default=None, ge=1)
    recommend_relaxation: bool = False
    relaxation_type: Optional[str] = None
    recommend_game: bool = False
    exit_requested: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""

    @classmethod
    def from_agent_route(cls, route: dict | None) -> "PolicyDecision":
        route = route or {}
        scale_action_raw = str(route.get("scale_action", "none")).lower()
        scale_action = ScaleAction(scale_action_raw) if scale_action_raw in ScaleAction._value2member_map_ else ScaleAction.NONE
        scale_name = cls._normalize_scale(route.get("scale"))
        recommend_relaxation = bool(route.get("recommend_relaxation"))
        recommend_game = bool(route.get("recommend_game"))
        exit_requested = bool(route.get("exit_intent"))
        if exit_requested:
            action = DialogueAction.EXIT
        elif recommend_game:
            action = DialogueAction.RECOMMEND_GAME
        elif recommend_relaxation:
            action = DialogueAction.RECOMMEND_RELAXATION
        elif scale_action is ScaleAction.START:
            action = DialogueAction.START_SCALE
        elif scale_action is ScaleAction.CONTINUE:
            action = DialogueAction.CONTINUE_SCALE
        else:
            action = DialogueAction.CHAT
        item = route.get("item")
        return cls(
            action=action,
            scale_action=scale_action,
            scale_name=scale_name,
            scale_item=item if isinstance(item, int) and item > 0 else None,
            recommend_relaxation=recommend_relaxation,
            relaxation_type=route.get("relaxation_type"),
            recommend_game=recommend_game,
            exit_requested=exit_requested,
            confidence=float(route.get("confidence", 0.0) or 0.0),
            reason=str(route.get("reason", "") or ""),
        )

    @staticmethod
    def _normalize_scale(value: object) -> Optional[str]:
        if not value:
            return None
        normalized = str(value).upper().replace(" ", "")
        normalized = normalized.replace("PHQ9", "PHQ-9").replace("GAD7", "GAD-7").replace("PCL5", "PCL-5")
        return normalized if normalized in {"PHQ-9", "GAD-7", "PCL-5"} else None
