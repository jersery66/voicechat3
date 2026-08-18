"""Phase 1 RelaxationRuntime state-machine tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from relaxation.catalog import RelaxationCatalog, build_default_catalog
from relaxation.contracts import RelaxationState
from relaxation.runtime import RelaxationRuntime, RelaxationRuntimeError


def test_inactive_enters_center_then_runs_enabled_content():
    runtime = RelaxationRuntime(build_default_catalog())

    assert runtime.snapshot().state is RelaxationState.INACTIVE
    center = runtime.enter_center()
    assert center.state is RelaxationState.CENTER
    running = runtime.start_content("breathing")
    assert running.state is RelaxationState.RUNNING
    assert running.selected_content_id == "breathing"
    assert running.content_type.value == "EXERCISE"


def test_invalid_start_from_inactive_is_rejected():
    runtime = RelaxationRuntime(build_default_catalog())
    with pytest.raises(RelaxationRuntimeError, match="CENTER"):
        runtime.start_content("breathing")


def test_complete_returns_to_center_and_clears_active_content():
    runtime = RelaxationRuntime(build_default_catalog())
    runtime.enter_center()
    runtime.start_content("breathing")
    completed = runtime.complete_content()

    assert completed.state is RelaxationState.CENTER
    assert completed.selected_content_id is None
    assert completed.content_type is None
    assert completed.completed is True
    assert completed.cancelled is False


def test_cancel_returns_to_center_and_records_reason():
    runtime = RelaxationRuntime(build_default_catalog())
    runtime.enter_center()
    runtime.start_content("breathing")
    cancelled = runtime.cancel_content("user_exit")

    assert cancelled.state is RelaxationState.CENTER
    assert cancelled.selected_content_id is None
    assert cancelled.cancelled is True
    assert cancelled.cancel_reason == "user_exit"


def test_center_exits_to_returning_then_finalizes_to_inactive():
    runtime = RelaxationRuntime(build_default_catalog())
    runtime.enter_center()
    returning = runtime.exit_to_conversation()
    assert returning.state is RelaxationState.RETURNING
    inactive = runtime.finalize_return()
    assert inactive.state is RelaxationState.INACTIVE
    assert inactive.relaxation_session_id is None


def test_running_cannot_be_overwritten_by_enter_or_second_start():
    runtime = RelaxationRuntime(build_default_catalog())
    runtime.enter_center()
    runtime.start_content("breathing")
    with pytest.raises(RelaxationRuntimeError, match="RUNNING"):
        runtime.enter_center()
    with pytest.raises(RelaxationRuntimeError, match="RUNNING"):
        runtime.start_content("bubble_pop")


def test_disabled_or_unknown_content_is_rejected():
    catalog = RelaxationCatalog([
        build_default_catalog().require("breathing").model_copy(update={"enabled": False})
    ])
    runtime = RelaxationRuntime(catalog)
    runtime.enter_center()
    with pytest.raises(RelaxationRuntimeError, match="disabled"):
        runtime.start_content("breathing")
    with pytest.raises(RelaxationRuntimeError, match="unknown"):
        runtime.start_content("not_registered")


def test_snapshot_is_immutable():
    runtime = RelaxationRuntime(build_default_catalog())
    snapshot = runtime.enter_center()
    with pytest.raises(ValidationError):
        snapshot.state = RelaxationState.RUNNING
