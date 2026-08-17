"""Generation-scoped safety boundary immediately before UI/TTS delivery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from conversation.contracts import TurnAction
from core.tags import (
    _RE_END_TAG,
    _RE_PIPE_TAG,
    _RE_REC_TAG,
    _RE_SCALE_TAG,
    _RE_THINK,
    _contains_internal_leak,
)


@dataclass(frozen=True)
class GuardContext:
    generation_id: int
    turn_action: TurnAction = TurnAction.CHAT
    active_scale: str | None = None
    current_scale_item: int | None = None
    max_primary_questions: int = 1


@dataclass
class GenerationGuardState:
    primary_question_count: int = 0
    blocked_reason: str | None = None


@dataclass(frozen=True)
class GuardResult:
    status: str
    text: str
    reason: str = ""


_FORBIDDEN_CONTROL = (_RE_END_TAG, _RE_REC_TAG, _RE_SCALE_TAG, _RE_PIPE_TAG, _RE_THINK)
_QUESTION_MARKS = re.compile(r"[?？]")
_SCALE_LEAKS = ("PHQ-9", "GAD-7", "PCL-5", "PHQ9", "GAD7", "PCL5", "量表", "问卷", "评分", "分数")


class PreDeliveryGuard:
    """Pure admission check; it never changes TurnDecision or domain state."""

    def evaluate(
        self,
        text: str,
        *,
        context: GuardContext,
        state: GenerationGuardState,
    ) -> GuardResult:
        raw = str(text or "").strip()
        if not raw:
            state.blocked_reason = "empty_sentence"
            return GuardResult("BLOCK", "", "empty_sentence")
        if any(pattern.search(raw) for pattern in _FORBIDDEN_CONTROL):
            state.blocked_reason = "legacy_control_or_thinking_tag"
            return GuardResult("BLOCK", "", state.blocked_reason)
        if _contains_internal_leak(raw):
            state.blocked_reason = "internal_strategy_leak"
            return GuardResult("BLOCK", "", state.blocked_reason)
        if context.turn_action in (TurnAction.START_SCALE, TurnAction.CONTINUE_SCALE):
            if any(term in raw for term in _SCALE_LEAKS):
                state.blocked_reason = "scale_wording_leak"
                return GuardResult("BLOCK", "", state.blocked_reason)
        question_count = len(_QUESTION_MARKS.findall(raw))
        if question_count:
            if state.primary_question_count + question_count > max(0, context.max_primary_questions):
                state.blocked_reason = "primary_question_budget_exceeded"
                return GuardResult("BLOCK", "", state.blocked_reason)
            state.primary_question_count += question_count
        return GuardResult("ALLOW", raw)
