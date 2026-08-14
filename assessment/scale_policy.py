"""Pure scale-entry policy; item progression remains owned by ScaleRuntime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from services.scales import get_scale_manager


@dataclass(frozen=True)
class ScaleDirective:
    action: str
    scale_name: Optional[str] = None
    item: Optional[int] = None


class ScalePolicy:
    """Translate router suggestions without allowing it to take item ownership."""

    def decide(self, *, route: dict | None, active_scale: Optional[str],
               active_item: int, waiting_for_answer: bool) -> ScaleDirective:
        if active_scale and waiting_for_answer:
            return ScaleDirective("keep_current", active_scale, active_item)

        route = route or {}
        action = str(route.get("scale_action", "none")).lower()
        requested_scale = self._normalize_scale(route.get("scale"))
        if active_scale and action == "continue":
            return ScaleDirective("continue", active_scale, active_item)
        if not active_scale and action == "start" and requested_scale:
            # Item progression is owned by ScaleRuntime; Router item hints
            # are intentionally ignored.
            return ScaleDirective("start", requested_scale, 1)
        if action == "pause" and active_scale:
            return ScaleDirective("pause", active_scale, active_item)
        return ScaleDirective("none")

    @staticmethod
    def _normalize_scale(value: object) -> Optional[str]:
        if not value:
            return None
        normalized = str(value).upper().replace(" ", "")
        normalized = normalized.replace("PHQ9", "PHQ-9").replace("GAD7", "GAD-7").replace("PCL5", "PCL-5")
        return normalized if normalized in get_scale_manager().get_scale_names() else None
