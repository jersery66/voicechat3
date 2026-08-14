"""Deterministic owner of the active assessment-scale state.

This module deliberately contains no routing, policy, language-model, UI,
report, or external-service logic.  Natural-language answers are interpreted outside
the Runtime and only validated structured scores enter ``accept_answer``.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple

from services.scales import ScaleDefinition, get_scale_manager


@dataclass(frozen=True)
class ScaleRuntimeSnapshot:
    """Immutable read model of all mutable questionnaire state."""

    active_scale: Optional[str]
    current_item: Optional[int]
    waiting_for_answer: bool
    answers_by_scale: Mapping[str, Mapping[int, int]]
    completed_scales: Tuple[str, ...]
    paused: bool
    resume_item: Optional[int]
    administered_scales: Tuple[str, ...]


@dataclass(frozen=True)
class RuntimeUpdate:
    """Result of one deterministic Runtime command."""

    status: str
    snapshot: ScaleRuntimeSnapshot
    accepted: bool = False
    completed: bool = False
    reason: str = ""


@dataclass(frozen=True)
class IncompleteScaleSnapshot:
    """Read-only incomplete-scale information for UI/report adapters."""

    scale_name: str
    total: int
    answered: int
    remaining_questions: Tuple[str, ...]
    remaining_nums: Tuple[int, ...]


class ScaleRuntime:
    """Single mutable owner for one session's questionnaire state.

    Item selection is always derived from the canonical definition and
    existing answers, never from an external action proposal.
    """

    def __init__(self) -> None:
        self._manager = get_scale_manager()
        self.reset()

    def reset(self) -> ScaleRuntimeSnapshot:
        """Clear scale administration state without touching session state."""
        self._active_scale: Optional[str] = None
        self._current_item: Optional[int] = None
        self._waiting_for_answer = False
        self._answers_by_scale: dict[str, dict[int, int]] = {}
        self._completed_scales: set[str] = set()
        self._paused = False
        self._resume_item: Optional[int] = None
        self._administered_scales: set[str] = set()
        return self.snapshot()

    def start(self, scale_name: str) -> RuntimeUpdate:
        """Start or resume an incomplete registered scale.

        Existing answers are preserved.  A completed scale cannot be started
        again until :meth:`reset` is called.
        """
        definition = self._definition(scale_name)
        if definition is None:
            return self._update("rejected", reason="unknown_scale")
        if scale_name in self._completed_scales:
            return self._update("rejected", reason="scale_completed")
        if self._active_scale is not None:
            return self._update("rejected", reason="active_scale_exists")

        answers = self._answers_by_scale.setdefault(scale_name, {})
        next_item = self._first_unanswered(scale_name)
        if next_item is None:
            self._completed_scales.add(scale_name)
            return self._update("rejected", reason="scale_completed")

        self._active_scale = scale_name
        self._current_item = next_item
        self._waiting_for_answer = True
        self._paused = False
        self._resume_item = None
        self._administered_scales.add(scale_name)
        return self._update("started", accepted=True)

    def present_current_item(self) -> RuntimeUpdate:
        """Mark the current Runtime-selected item as awaiting an answer."""
        if self._active_scale is None or self._current_item is None:
            return self._update("rejected", reason="no_active_scale")
        if self._paused:
            return self._update("rejected", reason="scale_paused")
        self._waiting_for_answer = True
        return self._update("waiting")

    def accept_answer(
        self,
        item: int,
        score: int,
        *,
        scale_name: Optional[str] = None,
    ) -> RuntimeUpdate:
        """Accept one already-interpreted, canonical answer."""
        if self._active_scale is None or self._current_item is None:
            return self._update("rejected", reason="no_active_scale")
        scale_name = scale_name or self._active_scale
        if scale_name != self._active_scale:
            return self._update("rejected", reason="wrong_scale")
        if self._paused:
            return self._update("rejected", reason="scale_paused")
        if not self._waiting_for_answer:
            return self._update("rejected", reason="not_waiting")
        if item != self._current_item:
            return self._update("rejected", reason="wrong_item")

        answers = self._answers_by_scale.setdefault(scale_name, {})
        if item in answers:
            return self._update("rejected", reason="duplicate_answer")
        if not self._manager.validate_answer(scale_name, item=item, score=score):
            return self._update("rejected", reason="invalid_score")

        answers[item] = score
        next_item = self._first_unanswered(scale_name)
        if next_item is None:
            self._completed_scales.add(scale_name)
            self._active_scale = None
            self._current_item = None
            self._waiting_for_answer = False
            self._paused = False
            self._resume_item = None
            return self._update("completed", accepted=True, completed=True)

        self._current_item = next_item
        self._waiting_for_answer = False
        self._resume_item = None
        return self._update("accepted", accepted=True)

    def request_clarification(self) -> RuntimeUpdate:
        """Keep the current item waiting without recording a score."""
        if self._active_scale is None or self._current_item is None:
            return self._update("rejected", reason="no_active_scale")
        if self._paused:
            return self._update("rejected", reason="scale_paused")
        self._waiting_for_answer = True
        return self._update("clarification_required")

    def pause(self) -> RuntimeUpdate:
        """Pause the active scale while preserving its actual next item."""
        if self._active_scale is None or self._current_item is None:
            return self._update("rejected", reason="no_active_scale")
        self._paused = True
        self._resume_item = self._first_unanswered(self._active_scale)
        self._waiting_for_answer = False
        return self._update("paused")

    def resume(self) -> RuntimeUpdate:
        """Resume the first unanswered item, ignoring stale caller hints."""
        if self._active_scale is None:
            return self._update("rejected", reason="no_active_scale")
        next_item = self._first_unanswered(self._active_scale)
        if next_item is None:
            completed_name = self._active_scale
            self._completed_scales.add(completed_name)
            self._active_scale = None
            self._current_item = None
            self._waiting_for_answer = False
            self._paused = False
            self._resume_item = None
            return self._update("completed", completed=True)
        self._current_item = next_item
        self._paused = False
        self._resume_item = None
        self._waiting_for_answer = True
        return self._update("resumed", accepted=True)

    def snapshot(self) -> ScaleRuntimeSnapshot:
        """Return a defensive immutable snapshot of the Runtime state."""
        answers = MappingProxyType({
            scale_name: MappingProxyType(dict(scale_answers))
            for scale_name, scale_answers in self._answers_by_scale.items()
        })
        return ScaleRuntimeSnapshot(
            active_scale=self._active_scale,
            current_item=self._current_item,
            waiting_for_answer=self._waiting_for_answer,
            answers_by_scale=answers,
            completed_scales=tuple(sorted(self._completed_scales)),
            paused=self._paused,
            resume_item=self._resume_item,
            administered_scales=tuple(sorted(self._administered_scales)),
        )

    @property
    def active_scale(self) -> Optional[str]:
        return self.snapshot().active_scale

    @property
    def current_item(self) -> Optional[int]:
        return self.snapshot().current_item

    @property
    def waiting_for_answer(self) -> bool:
        return self.snapshot().waiting_for_answer

    @property
    def paused(self) -> bool:
        return self.snapshot().paused

    @property
    def resume_item(self) -> Optional[int]:
        return self.snapshot().resume_item

    @property
    def answers_by_scale(self) -> Mapping[str, Mapping[int, int]]:
        return self.snapshot().answers_by_scale

    @property
    def completed_scales(self) -> Tuple[str, ...]:
        return self.snapshot().completed_scales

    @property
    def administered_scales(self) -> Tuple[str, ...]:
        return self.snapshot().administered_scales

    def get_incomplete_scales(self) -> Tuple[IncompleteScaleSnapshot, ...]:
        """Return immutable incomplete-scale views derived from Runtime data."""
        candidates = set(self._administered_scales)
        candidates.update(self._answers_by_scale)
        if self._active_scale:
            candidates.add(self._active_scale)

        incomplete: list[IncompleteScaleSnapshot] = []
        for scale_name in sorted(candidates):
            definition = self._definition(scale_name)
            if definition is None or scale_name in self._completed_scales:
                continue
            answers = self._answers_by_scale.get(scale_name, {})
            remaining_nums = tuple(
                item for item in range(1, definition.item_count + 1)
                if item not in answers
            )
            if not remaining_nums:
                continue
            incomplete.append(IncompleteScaleSnapshot(
                scale_name=scale_name,
                total=definition.item_count,
                answered=len(answers),
                remaining_questions=tuple(
                    definition.questions[item - 1] for item in remaining_nums
                ),
                remaining_nums=remaining_nums,
            ))
        return tuple(incomplete)

    def get_results(self) -> Mapping[str, Mapping[str, Any]]:
        """Return immutable report results derived from canonical definitions."""
        candidates = set(self._administered_scales)
        candidates.update(self._answers_by_scale)
        results: dict[str, Mapping[str, Any]] = {}
        for scale_name in sorted(candidates):
            definition = self._definition(scale_name)
            if definition is None:
                continue
            answers = self._answers_by_scale.get(scale_name, {})
            ordered_scores = [
                answers[item]
                for item in range(1, definition.item_count + 1)
                if item in answers
            ]
            summary = self._manager.score_scale(scale_name, ordered_scores)
            labels = dict(definition.options)
            items = tuple(MappingProxyType({
                "q_num": item,
                "question": definition.questions[item - 1],
                "score": answers.get(item),
                "label": labels.get(answers.get(item)),
                "answered": item in answers,
            }) for item in range(1, definition.item_count + 1))
            completed = scale_name in self._completed_scales
            missing_items = tuple(
                item for item in range(1, definition.item_count + 1)
                if item not in answers
            )
            results[scale_name] = MappingProxyType({
                "scale_name": scale_name,
                "completed": completed,
                "answered": len(answers),
                "total_items": definition.item_count,
                "total_score": summary.get("total"),
                "max_score": definition.max_score,
                "severity": summary.get("severity", "") if completed else "未完成，暂不判定",
                "total_score_label": "总分" if completed else "当前累计分",
                "missing_items": missing_items,
                "items": items,
            })
        return MappingProxyType(results)

    # Legacy direct-call compatibility.  Production Pipeline migration must
    # use the typed commands above rather than writing Runtime internals.
    def record_answer(self, item: int, score: int) -> bool:
        if self._active_scale is None:
            return False
        return self.accept_answer(
            scale_name=self._active_scale,
            item=item,
            score=score,
        ).accepted

    def next_item(self, *, total_items: int) -> Optional[int]:
        del total_items
        if self._active_scale is None:
            return None
        next_item = self._first_unanswered(self._active_scale)
        if next_item is not None:
            self._current_item = next_item
        return next_item

    def _definition(self, scale_name: str) -> Optional[ScaleDefinition]:
        return self._manager.get_scale_definition(scale_name)

    def _first_unanswered(self, scale_name: str) -> Optional[int]:
        definition = self._definition(scale_name)
        if definition is None:
            return None
        answers = self._answers_by_scale.get(scale_name, {})
        for item in range(1, definition.item_count + 1):
            if item not in answers:
                return item
        return None

    def _update(
        self,
        status: str,
        *,
        accepted: bool = False,
        completed: bool = False,
        reason: str = "",
    ) -> RuntimeUpdate:
        return RuntimeUpdate(
            status=status,
            snapshot=self.snapshot(),
            accepted=accepted,
            completed=completed,
            reason=reason,
        )
