"""Deterministic, standalone ActivityRuntime for future activities."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Mapping
from uuid import uuid4

from .catalog import ActivityCatalog
from .contracts import (
    ActivityResult,
    ActivityRuntimeError,
    ActivityRuntimeSnapshot,
    ActivityStatus,
    FrozenDict,
)


class ActivityRuntime:
    """Single writer for activity-internal state; not wired to production yet."""

    def __init__(
        self,
        catalog: ActivityCatalog,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.catalog = catalog
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._lock = RLock()
        self._state = ActivityRuntimeSnapshot()
        self._history: list[ActivityResult] = []
        self._session_counts: dict[str, int] = {}

    def snapshot(self) -> ActivityRuntimeSnapshot:
        with self._lock:
            return self._copy_snapshot(self._state)

    def start(self, activity_id: str, *, accepted: bool) -> ActivityRuntimeSnapshot:
        with self._lock:
            definition = self.catalog.get(activity_id)
            if definition is None:
                raise ActivityRuntimeError(f"unknown activity: {activity_id}")
            if accepted is not True:
                raise ActivityRuntimeError("explicit opt-in required before start")
            if self._state.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
                raise ActivityRuntimeError("activity already active")
            if self._session_counts.get(activity_id, 0) >= definition.max_per_session:
                raise ActivityRuntimeError("max_per_session reached")
            now = self._timestamp()
            self._session_counts[activity_id] = self._session_counts.get(activity_id, 0) + 1
            self._state = ActivityRuntimeSnapshot(
                activity_session_id=self._id_factory(),
                active_activity=definition.id,
                activity_category=definition.category,
                status=ActivityStatus.ACTIVE,
                current_step=1,
                started_at=now,
                metadata={"result_schema": definition.result_schema},
            )
            return self._copy_snapshot(self._state)

    def submit_response(
        self,
        response: Mapping[str, Any],
        *,
        advance: bool = True,
    ) -> ActivityRuntimeSnapshot:
        with self._lock:
            self._require_status(ActivityStatus.ACTIVE)
            if not isinstance(response, Mapping):
                raise ActivityRuntimeError("response must be a mapping")
            values = dict(self._state.responses)
            step = self._state.current_step or 1
            values[f"step_{step}"] = deepcopy(dict(response))
            next_step = step + 1 if advance else step
            self._state = self._replace_snapshot(responses=values, current_step=next_step)
            return self._copy_snapshot(self._state)

    def pause(self, reason: str = "") -> ActivityRuntimeSnapshot:
        with self._lock:
            self._require_status(ActivityStatus.ACTIVE)
            self._state = self._replace_snapshot(
                status=ActivityStatus.PAUSED,
                paused=True,
                metadata={**dict(self._state.metadata), "pause_reason": reason},
            )
            return self._copy_snapshot(self._state)

    def resume(self) -> ActivityRuntimeSnapshot:
        with self._lock:
            self._require_status(ActivityStatus.PAUSED)
            self._state = self._replace_snapshot(status=ActivityStatus.ACTIVE, paused=False)
            return self._copy_snapshot(self._state)

    def complete(self) -> ActivityRuntimeSnapshot:
        with self._lock:
            self._require_status(ActivityStatus.ACTIVE)
            now = self._timestamp()
            self._state = self._replace_snapshot(
                status=ActivityStatus.COMPLETED,
                completed=True,
                completed_at=now,
            )
            self._history.append(self._result_from_state())
            return self._copy_snapshot(self._state)

    def cancel(self, reason: str) -> ActivityRuntimeSnapshot:
        with self._lock:
            if self._state.status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
                raise ActivityRuntimeError("activity is not active")
            now = self._timestamp()
            self._state = self._replace_snapshot(
                status=ActivityStatus.CANCELLED,
                cancelled=True,
                paused=False,
                cancel_reason=reason or "cancelled",
                metadata={**dict(self._state.metadata), "cancelled_at": now},
            )
            self._history.append(self._result_from_state())
            return self._copy_snapshot(self._state)

    def close(self) -> ActivityRuntimeSnapshot:
        """Close a terminal snapshot without deleting committed history."""
        with self._lock:
            if self._state.status not in {ActivityStatus.COMPLETED, ActivityStatus.CANCELLED}:
                raise ActivityRuntimeError("only a completed or cancelled activity can close")
            self._state = ActivityRuntimeSnapshot()
            return self._copy_snapshot(self._state)

    def reset_session(self) -> None:
        with self._lock:
            if self._state.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
                raise ActivityRuntimeError("cannot reset while activity is active")
            self._state = ActivityRuntimeSnapshot()
            self._history.clear()
            self._session_counts.clear()

    def results(self) -> tuple[ActivityResult, ...]:
        with self._lock:
            return tuple(self._history)

    def _require_status(self, expected: ActivityStatus) -> None:
        if self._state.status is ActivityStatus.CANCELLED:
            raise ActivityRuntimeError("activity is cancelled")
        if self._state.status is not expected:
            raise ActivityRuntimeError(f"activity is not {expected.value.lower()}")

    def _timestamp(self) -> str:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    def _replace_snapshot(self, **changes: Any) -> ActivityRuntimeSnapshot:
        values = self._state.model_dump()
        values.update(changes)
        return ActivityRuntimeSnapshot(**values)

    def _copy_snapshot(self, snapshot: ActivityRuntimeSnapshot) -> ActivityRuntimeSnapshot:
        values = snapshot.model_dump()
        values["responses"] = deepcopy(dict(snapshot.responses))
        values["metadata"] = deepcopy(dict(snapshot.metadata))
        return ActivityRuntimeSnapshot(**values)

    def _result_from_state(self) -> ActivityResult:
        state = self._state
        return ActivityResult(
            activity_session_id=state.activity_session_id or "",
            activity_id=state.active_activity or "",
            completion_status=state.status,
            responses=deepcopy(dict(state.responses)),
            started_at=state.started_at,
            completed_at=state.completed_at,
            cancelled_at=state.metadata.get("cancelled_at") if state.cancelled else None,
            cancel_reason=state.cancel_reason,
            pre_rating=state.pre_rating,
            post_rating=state.post_rating,
        )
