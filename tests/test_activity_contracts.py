"""Phase A activity contracts are immutable and bounded."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from activity.contracts import (
    ActivityCandidate,
    ActivityDefinition,
    ActivityResult,
    ActivityStatus,
    ActivityRuntimeSnapshot,
)


def _definition(**overrides):
    values = {
        "id": "refusal_rehearsal",
        "display_name": "拒绝挑战",
        "category": "recovery_skill",
        "target_need": "refusal_skill",
        "min_need_score": 0.70,
        "min_activity_readiness": 0.65,
        "allowed_user_load": ("LOW", "MODERATE"),
        "allowed_conversation_stages": ("EXPLORATION", "RECOVERY"),
        "opt_in_required": True,
        "proactive_allowed": True,
        "max_per_session": 1,
        "cooldown_rounds": 4,
        "expected_duration_minutes": 5,
        "can_interrupt_scale": False,
        "resume_scale_after": True,
        "uses_media": False,
        "requires_voice_input": True,
        "result_schema": "refusal_rehearsal.v1",
        "evidence_status": "supportive_skill_practice",
    }
    values.update(overrides)
    return ActivityDefinition(**values)


def test_definition_and_candidate_are_immutable_observations():
    definition = _definition()
    candidate = ActivityCandidate(
        activity_id=definition.id,
        score=0.86,
        target_need=definition.target_need,
        reason="refusal need is high",
    )

    with pytest.raises(ValidationError):
        definition.display_name = "changed"
    with pytest.raises(ValidationError):
        candidate.score = 0.1


def test_definition_rejects_unknown_need_and_non_opt_in_activity():
    with pytest.raises(ValidationError):
        _definition(target_need="relapse_risk")
    with pytest.raises(ValidationError):
        _definition(opt_in_required=False)


def test_definition_rejects_out_of_range_thresholds_and_invalid_id():
    with pytest.raises(ValidationError):
        _definition(id="Refusal Rehearsal")
    with pytest.raises(ValidationError):
        _definition(min_need_score=1.1)
    with pytest.raises(ValidationError):
        _definition(expected_duration_minutes=-1)


def test_result_and_snapshot_have_explicit_terminal_statuses():
    result = ActivityResult(
        activity_session_id="activity-1",
        activity_id="refusal_rehearsal",
        completion_status=ActivityStatus.CANCELLED,
        cancel_reason="user_exit",
    )
    snapshot = ActivityRuntimeSnapshot(
        activity_session_id=result.activity_session_id,
        active_activity=result.activity_id,
        status=ActivityStatus.CANCELLED,
        cancelled=True,
        cancel_reason=result.cancel_reason,
    )

    assert result.completion_status is ActivityStatus.CANCELLED
    assert snapshot.cancelled is True
    assert snapshot.completed is False
