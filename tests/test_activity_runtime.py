"""Deterministic Phase A ActivityRuntime state ownership tests."""

from __future__ import annotations

import pytest

from activity.catalog import build_default_catalog
from activity.contracts import ActivityRuntimeError, ActivityStatus
from activity.runtime import ActivityRuntime


def test_activity_requires_explicit_opt_in_and_starts_once():
    runtime = ActivityRuntime(build_default_catalog())

    with pytest.raises(ActivityRuntimeError, match="opt-in"):
        runtime.start("trigger_detective", accepted=False)

    snapshot = runtime.start("trigger_detective", accepted=True)
    assert snapshot.status is ActivityStatus.ACTIVE
    assert snapshot.current_step == 1
    with pytest.raises(ActivityRuntimeError, match="already active"):
        runtime.start("refusal_rehearsal", accepted=True)


def test_runtime_owns_step_progression_and_snapshot_is_read_only():
    runtime = ActivityRuntime(build_default_catalog())
    runtime.start("coping_toolbox", accepted=True)

    first = runtime.submit_response({"distress": ["walk"]})
    assert first.current_step == 2
    assert first.responses["step_1"] == {"distress": ["walk"]}

    completed = runtime.submit_response({"sleep": ["breathing"]}, advance=False)
    assert completed.current_step == 2
    completed = runtime.complete()
    assert completed.status is ActivityStatus.COMPLETED
    assert completed.completed is True

    with pytest.raises(TypeError):
        completed.responses["step_1"] = "mutated"


def test_cancelled_activity_cannot_resume_or_complete():
    runtime = ActivityRuntime(build_default_catalog())
    runtime.start("refusal_rehearsal", accepted=True)
    cancelled = runtime.cancel("user_exit")

    assert cancelled.status is ActivityStatus.CANCELLED
    with pytest.raises(ActivityRuntimeError, match="cancelled"):
        runtime.resume()
    with pytest.raises(ActivityRuntimeError, match="cancelled"):
        runtime.complete()


def test_max_per_session_is_enforced_until_new_session_reset():
    runtime = ActivityRuntime(build_default_catalog())
    runtime.start("trigger_detective", accepted=True)
    runtime.complete()
    runtime.close()

    with pytest.raises(ActivityRuntimeError, match="max_per_session"):
        runtime.start("trigger_detective", accepted=True)

    runtime.reset_session()
    assert runtime.snapshot().status is ActivityStatus.INACTIVE
    assert runtime.start("trigger_detective", accepted=True).status is ActivityStatus.ACTIVE


def test_unknown_activity_and_invalid_response_fail_closed():
    runtime = ActivityRuntime(build_default_catalog())
    with pytest.raises(ActivityRuntimeError, match="unknown"):
        runtime.start("unknown", accepted=True)
    runtime.start("trigger_detective", accepted=True)
    with pytest.raises(ActivityRuntimeError, match="mapping"):
        runtime.submit_response("not a mapping")
