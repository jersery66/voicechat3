"""Canonical answer bookkeeping for a single active assessment scale."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ScaleRuntime:
    """State holder used by the future scale orchestration adapter.

    It deliberately owns neither natural-language scoring nor UI prompts.
    The pipeline keeps those compatibility behaviours until its scale branch
    is migrated behind this runtime.
    """

    active_scale: Optional[str] = None
    current_item: int = 1
    answers: dict[int, int] = field(default_factory=dict)

    def start(self, scale_name: str, *, item: int = 1) -> None:
        self.active_scale = scale_name
        self.current_item = item
        self.answers = {}

    def record_answer(self, item: int, score: int) -> bool:
        if self.active_scale is None or item != self.current_item or score not in {0, 1, 2, 3, 4}:
            return False
        self.answers[item] = score
        return True

    def next_item(self, *, total_items: int) -> Optional[int]:
        if self.active_scale is None:
            return None
        for item in range(1, total_items + 1):
            if item not in self.answers:
                self.current_item = item
                return item
        return None
