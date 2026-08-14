"""Pure observations used by :mod:`conversation.turn_policy`."""

from __future__ import annotations

from conversation.contracts import TurnSignals, TurnStateSnapshot
from core.scoring import is_scale_interruption_text, is_user_explicit_end_text

__all__ = ["TurnSignals", "collect_turn_signals"]


def collect_turn_signals(
    user_text: str,
    snapshot: TurnStateSnapshot,
    *,
    deterministic_scale_candidate: str | None = None,
    legacy_relaxation_candidate: str | None = None,
    legacy_game_candidate: bool = False,
) -> TurnSignals:
    """Inspect one turn without mutating state or calling a model.

    The existing deterministic detectors are intentionally used as facts only;
    ``TurnPolicy`` decides whether any fact becomes an executable action.
    """
    text = user_text or ""
    interruption = bool(snapshot.active_scale) and is_scale_interruption_text(text)
    return TurnSignals(
        explicit_end_requested=is_user_explicit_end_text(text),
        active_scale_pause_requested=interruption,
        active_scale_refusal=interruption,
        deterministic_scale_candidate=deterministic_scale_candidate,
        legacy_relaxation_candidate=legacy_relaxation_candidate,
        legacy_game_candidate=bool(legacy_game_candidate),
    )
