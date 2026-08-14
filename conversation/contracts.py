"""Typed contracts for the Phase 2 turn-authority boundary.

The active production contracts are ``RouterProposal`` (a non-executable
suggestion) and ``TurnDecision`` (the single executable decision).  The
``PolicyDecision`` class at the end of this module is retained only as a
deprecated compatibility value for old integrations; production code must
not construct it.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _known_scales() -> tuple[str, ...]:
    """Read the existing scale registry without duplicating its names."""
    try:
        from services.scales import SCALES

        return tuple(SCALES.keys())
    except Exception:
        # Contract validation must remain usable in small protocol-only tests.
        return ("PHQ-9", "GAD-7", "PCL-5")


def _normalize_scale(value: Any) -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None
    normalized = str(value).upper().replace(" ", "")
    normalized = normalized.replace("PHQ9", "PHQ-9")
    normalized = normalized.replace("GAD7", "GAD-7")
    normalized = normalized.replace("PCL5", "PCL-5")
    return normalized if normalized in _known_scales() else None


class _FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class RouterAction(str, Enum):
    """The intentionally narrow action vocabulary emitted by the Router."""

    CHAT = "chat"
    START_SCALE = "start_scale"
    RECOMMEND_RELAXATION = "recommend_relaxation"
    RECOMMEND_GAME = "recommend_game"
    END_SESSION = "end_session"


class RouterProposal(_FrozenContract):
    """A Router suggestion that cannot directly mutate runtime state."""

    action: RouterAction = RouterAction.CHAT
    scale_name: Optional[str] = None
    intervention_type: Optional[str] = None
    emotion: str = "neutral"
    intensity: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_rag: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""

    @model_validator(mode="after")
    def _validate_scale(self) -> "RouterProposal":
        if self.scale_name is not None and self.scale_name not in _known_scales():
            raise ValueError(f"unknown scale_name: {self.scale_name}")
        if self.action is RouterAction.START_SCALE and self.scale_name is None:
            raise ValueError("START_SCALE requires scale_name")
        return self

    @classmethod
    def fallback(cls, reason: str = "router_fallback") -> "RouterProposal":
        return cls(action=RouterAction.CHAT, confidence=0.0, reason=reason)

    @classmethod
    def from_legacy_route(cls, route: dict | None) -> "RouterProposal":
        """Adapt the pre-Phase-2 payload while discarding executable fields.

        Existing test doubles and deployment adapters may still return the old
        dictionary shape for a short migration period.  ``item``, score,
        urgency, and other control fields are deliberately never copied.
        """
        raw = dict(route or {})
        raw_action = str(raw.get("action") or raw.get("scale_action") or "chat").strip().lower()
        action_map = {
            "chat": RouterAction.CHAT,
            "none": RouterAction.CHAT,
            "start": RouterAction.START_SCALE,
            "start_scale": RouterAction.START_SCALE,
            # Continue and pause are state-derived decisions, not Router
            # actions.  They therefore enter the policy as a neutral proposal.
            "continue": RouterAction.CHAT,
            "continue_scale": RouterAction.CHAT,
            "pause": RouterAction.CHAT,
            "recommend_relaxation": RouterAction.RECOMMEND_RELAXATION,
            "recommend_game": RouterAction.RECOMMEND_GAME,
            "exit": RouterAction.END_SESSION,
            "end_session": RouterAction.END_SESSION,
        }
        action = action_map.get(raw_action)
        if action is None:
            return cls.fallback("router_fallback")
        # Legacy adapters sometimes kept the action as ``none`` while
        # exposing recommendation booleans.  Normalize those hints into the
        # proposal vocabulary without copying the old control fields.
        if raw_action in {"chat", "none"}:
            if raw.get("recommend_relaxation"):
                action = RouterAction.RECOMMEND_RELAXATION
            elif raw.get("recommend_game"):
                action = RouterAction.RECOMMEND_GAME
            elif raw.get("exit_intent"):
                action = RouterAction.END_SESSION

        scale = _normalize_scale(raw.get("scale_name") or raw.get("scale"))
        if raw_action in {"start", "start_scale"} and scale is None:
            return cls.fallback("invalid_scale")
        try:
            confidence = float(raw.get("confidence", 0.0) or 0.0)
            intensity = float(raw.get("intensity", 0.0) or 0.0)
        except (TypeError, ValueError):
            return cls.fallback("router_fallback")
        if not 0.0 <= confidence <= 1.0 or not 0.0 <= intensity <= 1.0:
            return cls.fallback("router_fallback")

        intervention = raw.get("intervention_type") or raw.get("relaxation_type")
        return cls(
            action=action,
            scale_name=scale,
            intervention_type=str(intervention) if intervention else None,
            emotion=str(raw.get("emotion") or "neutral"),
            intensity=intensity,
            needs_rag=bool(raw.get("needs_rag", action is RouterAction.CHAT)),
            confidence=confidence,
            reason=str(raw.get("reason") or ""),
        )


class TurnStateSnapshot(_FrozenContract):
    """Read-only view of the current legacy state for one user turn."""

    session_state: str = "CHATTING"
    round_count: int = Field(default=0, ge=0)
    active_scale: Optional[str] = None
    current_item: Optional[int] = Field(default=None, ge=1)
    waiting_for_answer: bool = False
    completed_scales: tuple[str, ...] = ()
    relaxation_used: bool = False
    # Policy fact: a proactive recommendation has already been offered in
    # this session.  This is distinct from ``relaxation_used`` (a completed
    # intervention/report fact) and is read-only at the turn boundary.
    proactive_relaxation_offered: bool = False
    game_active: bool = False
    time_limit_reached: bool = False


class TurnSignals(_FrozenContract):
    """Pure observations collected before a decision is formed."""

    explicit_end_requested: bool = False
    active_scale_pause_requested: bool = False
    active_scale_refusal: bool = False
    explicit_relaxation_requested: bool = False
    explicit_game_requested: bool = False
    deterministic_scale_candidate: Optional[str] = None
    proactive_relaxation_candidate: Optional[str] = None
    legacy_relaxation_candidate: Optional[str] = None
    legacy_game_candidate: bool = False


class TurnAction(str, Enum):
    """The only actions that the execution layer may receive."""

    CHAT = "chat"
    START_SCALE = "start_scale"
    CONTINUE_SCALE = "continue_scale"
    PAUSE_SCALE = "pause_scale"
    RECOMMEND_RELAXATION = "recommend_relaxation"
    RECOMMEND_GAME = "recommend_game"
    END_SESSION = "end_session"


class TurnDecision(_FrozenContract):
    """Single authoritative, immutable decision for one user turn."""

    action: TurnAction = TurnAction.CHAT
    scale_name: Optional[str] = None
    answered_item: Optional[int] = Field(default=None, ge=1)
    accepted_score: Optional[int] = Field(default=None, ge=0, le=4)
    next_item: Optional[int] = Field(default=None, ge=1)
    intervention_type: Optional[str] = None
    end_reason: Optional[str] = None
    needs_rag: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""

    @model_validator(mode="after")
    def _validate_action_fields(self) -> "TurnDecision":
        scale_fields = (self.scale_name, self.answered_item, self.accepted_score, self.next_item)
        if self.action is TurnAction.START_SCALE:
            if self.scale_name not in _known_scales():
                raise ValueError("START_SCALE requires a registered scale_name")
            if any(value is not None for value in (self.answered_item, self.accepted_score, self.next_item)):
                raise ValueError("START_SCALE cannot carry answer fields")
        elif self.action in (TurnAction.CONTINUE_SCALE, TurnAction.PAUSE_SCALE):
            if self.scale_name not in _known_scales():
                raise ValueError(f"{self.action.value} requires a registered scale_name")
        elif self.action is TurnAction.END_SESSION:
            if not self.end_reason:
                raise ValueError("END_SESSION requires end_reason")
            if any(value is not None for value in scale_fields):
                raise ValueError("END_SESSION cannot carry scale fields")
        elif self.action is TurnAction.CHAT:
            if any(value is not None for value in scale_fields) or self.end_reason is not None:
                raise ValueError("CHAT cannot carry control fields")
        elif self.action is TurnAction.RECOMMEND_GAME:
            if any(value is not None for value in scale_fields):
                raise ValueError("RECOMMEND_GAME cannot carry scale fields")
        return self


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
