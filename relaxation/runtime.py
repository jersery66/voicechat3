"""Small deterministic Relaxation Center lifecycle runtime."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Callable
from uuid import uuid4

from .catalog import RelaxationCatalog
from .contracts import (
    RelaxationContentType,
    RelaxationRuntimeError,
    RelaxationSnapshot,
    RelaxationState,
)


class RelaxationRuntime:
    """Single writer for Center/content lifecycle, not recommendations."""

    def __init__(
        self,
        catalog: RelaxationCatalog,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.catalog = catalog
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._lock = RLock()
        self._snapshot = RelaxationSnapshot()

    def snapshot(self) -> RelaxationSnapshot:
        with self._lock:
            return self._snapshot.model_copy(deep=True)

    def enter_center(self) -> RelaxationSnapshot:
        with self._lock:
            self._require_state(RelaxationState.INACTIVE)
            self._snapshot = RelaxationSnapshot(
                state=RelaxationState.CENTER,
                relaxation_session_id=self._id_factory(),
            )
            return self.snapshot()

    def start_content(self, content_id: str) -> RelaxationSnapshot:
        with self._lock:
            self._require_state(RelaxationState.CENTER)
            definition = self.catalog.get(content_id)
            if definition is None:
                raise RelaxationRuntimeError(f"unknown relaxation content: {content_id}")
            if not definition.enabled:
                raise RelaxationRuntimeError(f"relaxation content is disabled: {content_id}")
            if not definition.is_available:
                raise RelaxationRuntimeError(f"relaxation content is not available: {content_id}")
            self._snapshot = self._snapshot.model_copy(
                update={
                    "state": RelaxationState.RUNNING,
                    "selected_content_id": content_id,
                    "content_type": definition.category,
                    "started_at": self._timestamp(),
                    "ended_at": None,
                    "completed": False,
                    "cancelled": False,
                    "cancel_reason": None,
                }
            )
            return self.snapshot()

    def complete_content(self) -> RelaxationSnapshot:
        with self._lock:
            self._require_state(RelaxationState.RUNNING)
            self._snapshot = self._snapshot.model_copy(
                update={
                    "state": RelaxationState.CENTER,
                    "selected_content_id": None,
                    "content_type": None,
                    "ended_at": self._timestamp(),
                    "completed": True,
                    "cancelled": False,
                    "cancel_reason": None,
                }
            )
            return self.snapshot()

    def cancel_content(self, reason: str = "cancelled") -> RelaxationSnapshot:
        with self._lock:
            self._require_state(RelaxationState.RUNNING)
            self._snapshot = self._snapshot.model_copy(
                update={
                    "state": RelaxationState.CENTER,
                    "selected_content_id": None,
                    "content_type": None,
                    "ended_at": self._timestamp(),
                    "completed": False,
                    "cancelled": True,
                    "cancel_reason": reason or "cancelled",
                }
            )
            return self.snapshot()

    def return_to_center(self) -> RelaxationSnapshot:
        with self._lock:
            self._require_state(RelaxationState.CENTER)
            return self.snapshot()

    def exit_to_conversation(self) -> RelaxationSnapshot:
        with self._lock:
            self._require_state(RelaxationState.CENTER)
            self._snapshot = self._snapshot.model_copy(update={"state": RelaxationState.RETURNING})
            return self.snapshot()

    def finalize_return(self) -> RelaxationSnapshot:
        with self._lock:
            self._require_state(RelaxationState.RETURNING)
            self._snapshot = RelaxationSnapshot()
            return self.snapshot()

    def _require_state(self, expected: RelaxationState) -> None:
        if self._snapshot.state is RelaxationState.RUNNING and expected is not RelaxationState.RUNNING:
            raise RelaxationRuntimeError("cannot overwrite RUNNING content")
        if self._snapshot.state is not expected:
            raise RelaxationRuntimeError(
                f"operation requires {expected.value}, current state is {self._snapshot.state.value}"
            )

    def _timestamp(self) -> str:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
