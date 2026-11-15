"""Configuration-driven catalog for future support activities."""

from __future__ import annotations

from typing import Iterable

from .contracts import ActivityDefinition


class ActivityCatalog:
    """Immutable registry; it does not rank or start activities."""

    def __init__(self, definitions: Iterable[ActivityDefinition]) -> None:
        values = tuple(definitions)
        ids = [definition.id for definition in values]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate activity id in catalog")
        self._definitions = values
        self._by_id = {definition.id: definition for definition in values}

    @property
    def definitions(self) -> tuple[ActivityDefinition, ...]:
        return self._definitions

    def get(self, activity_id: str) -> ActivityDefinition | None:
        return self._by_id.get(activity_id)

    def require(self, activity_id: str) -> ActivityDefinition:
        definition = self.get(activity_id)
        if definition is None:
            raise KeyError(f"unknown activity: {activity_id}")
        return definition

    def __iter__(self):
        return iter(self._definitions)

    def __len__(self) -> int:
        return len(self._definitions)


def _definition(**values) -> ActivityDefinition:
    return ActivityDefinition(**values)


def build_default_catalog() -> ActivityCatalog:
    """Return the first six future modules plus existing activity identities."""
    all_loads = ("LOW", "MODERATE", "HIGH")
    all_stages = ("RAPPORT", "EXPLORATION", "ASSESSMENT", "STABILIZATION", "RECOVERY")
    return ActivityCatalog(
        [
            _definition(
                id="trigger_detective", display_name="诱因侦探", category="awareness",
                target_need="trigger_awareness", min_need_score=0.65,
                min_activity_readiness=0.60, allowed_user_load=("LOW", "MODERATE"),
                allowed_conversation_stages=("EXPLORATION", "RECOVERY"),
                max_per_session=1, cooldown_rounds=4, expected_duration_minutes=5,
                result_schema="trigger_detective.v1", evidence_status="supportive_skill_practice",
            ),
            _definition(
                id="refusal_rehearsal", display_name="拒绝挑战", category="recovery_skill",
                target_need="refusal_skill", min_need_score=0.70,
                min_activity_readiness=0.65, allowed_user_load=("LOW", "MODERATE"),
                allowed_conversation_stages=("EXPLORATION", "RECOVERY"),
                max_per_session=1, cooldown_rounds=4, expected_duration_minutes=5,
                requires_voice_input=True, result_schema="refusal_rehearsal.v1",
                evidence_status="supportive_skill_practice",
            ),
            _definition(
                id="coping_toolbox", display_name="我的应对工具箱", category="coping_skill",
                target_need="coping_skill", min_need_score=0.65,
                min_activity_readiness=0.60, allowed_user_load=("LOW", "MODERATE"),
                allowed_conversation_stages=("EXPLORATION", "RECOVERY"),
                max_per_session=1, cooldown_rounds=4, expected_duration_minutes=5,
                result_schema="coping_toolbox.v1", evidence_status="supportive_skill_practice",
            ),
            _definition(
                id="crossroads", display_name="岔路口", category="recovery_planning",
                target_need="recovery_planning", min_need_score=0.65,
                min_activity_readiness=0.65, allowed_user_load=("LOW", "MODERATE"),
                allowed_conversation_stages=("RECOVERY",), max_per_session=1,
                cooldown_rounds=6, expected_duration_minutes=7,
                result_schema="crossroads.v1", evidence_status="supportive_skill_practice",
            ),
            _definition(
                id="ten_minute_buffer", display_name="十分钟缓冲", category="stabilization",
                target_need="immediate_stabilization", min_need_score=0.65,
                min_activity_readiness=0.55, allowed_user_load=("HIGH", "MODERATE"),
                allowed_conversation_stages=all_stages, max_per_session=1,
                cooldown_rounds=6, expected_duration_minutes=10, can_interrupt_scale=True,
                result_schema="ten_minute_buffer.v1", evidence_status="supportive_skill_practice",
            ),
            _definition(
                id="change_balance", display_name="改变天平", category="change_motivation",
                target_need="change_motivation", min_need_score=0.65,
                min_activity_readiness=0.65, allowed_user_load=("LOW", "MODERATE"),
                allowed_conversation_stages=("RECOVERY",), max_per_session=1,
                cooldown_rounds=6, expected_duration_minutes=7,
                result_schema="change_balance.v1", evidence_status="supportive_skill_practice",
            ),
            # Existing runtime identities remain catalog-visible without changing
            # the current relaxation/video/game paths.
            _definition(
                id="breathing", display_name="呼吸放松", category="stabilization",
                target_need="immediate_stabilization", min_need_score=0.0,
                min_activity_readiness=0.0, allowed_user_load=all_loads,
                allowed_conversation_stages=all_stages, proactive_allowed=False,
                result_schema="existing_relaxation.v1", evidence_status="existing_runtime",
            ),
            _definition(
                id="muscle_relaxation", display_name="肌肉放松", category="stabilization",
                target_need="immediate_stabilization", min_need_score=0.0,
                min_activity_readiness=0.0, allowed_user_load=all_loads,
                allowed_conversation_stages=all_stages, proactive_allowed=False,
                result_schema="existing_relaxation.v1", evidence_status="existing_runtime",
            ),
            _definition(
                id="meditation", display_name="冥想正念", category="stabilization",
                target_need="immediate_stabilization", min_need_score=0.0,
                min_activity_readiness=0.0, allowed_user_load=all_loads,
                allowed_conversation_stages=all_stages, proactive_allowed=False,
                result_schema="existing_relaxation.v1", evidence_status="existing_runtime",
            ),
            _definition(
                id="video", display_name="放松视频", category="media",
                target_need="immediate_stabilization", min_need_score=0.0,
                min_activity_readiness=0.0, allowed_user_load=all_loads,
                allowed_conversation_stages=all_stages, proactive_allowed=False,
                uses_media=True, result_schema="existing_media.v1", evidence_status="existing_runtime",
            ),
            _definition(
                id="game", display_name="互动小游戏", category="media",
                target_need="coping_skill", min_need_score=0.0,
                min_activity_readiness=0.0, allowed_user_load=("LOW", "MODERATE"),
                allowed_conversation_stages=all_stages, proactive_allowed=False,
                uses_media=True, result_schema="existing_media.v1", evidence_status="existing_runtime",
            ),
        ]
    )
