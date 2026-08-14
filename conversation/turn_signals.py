"""Pure observations used by :mod:`conversation.turn_policy`."""

from __future__ import annotations

from conversation.contracts import TurnSignals, TurnStateSnapshot
from core.scoring import is_scale_interruption_text, is_user_explicit_end_text

__all__ = ["TurnSignals", "collect_turn_signals"]


_EXPLICIT_RELAXATION_PHRASES = (
    "想放松",
    "放松一下",
    "做个放松",
    "做放松训练",
    "放松训练",
    "呼吸练习",
    "呼吸放松",
    "肌肉放松",
    "冥想练习",
    "做冥想",
)

_EXPLICIT_GAME_PHRASES = (
    "想玩游戏",
    "想玩个游戏",
    "玩个游戏",
    "玩游戏",
    "开始游戏",
    "来个小游戏",
    "玩一局",
)


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    normalized = (text or "").strip().lower()
    return any(phrase in normalized for phrase in phrases)


def collect_turn_signals(
    user_text: str,
    snapshot: TurnStateSnapshot,
    *,
    deterministic_scale_candidate: str | None = None,
    proactive_relaxation_candidate: str | None = None,
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
        explicit_relaxation_requested=_contains_any(text, _EXPLICIT_RELAXATION_PHRASES),
        explicit_game_requested=_contains_any(text, _EXPLICIT_GAME_PHRASES),
        deterministic_scale_candidate=deterministic_scale_candidate,
        proactive_relaxation_candidate=proactive_relaxation_candidate,
        legacy_relaxation_candidate=legacy_relaxation_candidate,
        legacy_game_candidate=bool(legacy_game_candidate),
    )
