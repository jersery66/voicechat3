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

_RELAXATION_REQUEST_VERBS = ("我想", "想做", "想要", "我要", "要做", "来个", "可以做", "能不能", "试试", "试一下", "做个")
_RELAXATION_STATEMENT_PREFIXES = ("以前", "曾经", "过去", "老师教过", "做过", "对我没用")
_TYPED_RELAXATION_PHRASES = (
    ("breathing", ("呼吸练习", "呼吸训练", "呼吸放松", "深呼吸", "做个呼吸")),
    ("muscle", ("肌肉放松", "渐进性肌肉放松", "做肌肉放松")),
    ("meditation", ("冥想练习", "正念练习", "冥想", "正念")),
)


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    normalized = (text or "").strip().lower()
    return any(phrase in normalized for phrase in phrases)


def detect_explicit_relaxation_request(text: str) -> tuple[bool, str | None]:
    """Return a request/type pair without treating historical mentions as requests."""
    normalized = (text or "").strip().lower()
    if not normalized or any(normalized.startswith(prefix) for prefix in _RELAXATION_STATEMENT_PREFIXES):
        return False, None
    request_like = any(verb in normalized for verb in _RELAXATION_REQUEST_VERBS)
    for relaxation_type, phrases in _TYPED_RELAXATION_PHRASES:
        if request_like and any(phrase in normalized for phrase in phrases):
            return True, relaxation_type
    if _contains_any(normalized, _EXPLICIT_RELAXATION_PHRASES):
        return True, None
    return False, None


def collect_turn_signals(
    user_text: str,
    snapshot: TurnStateSnapshot,
    *,
    deterministic_scale_candidate: str | None = None,
    proactive_relaxation_candidate: str | None = None,
    legacy_relaxation_candidate: str | None = None,
    legacy_game_candidate: bool = False,
    semantic_ambiguity: bool = False,
    semantic_target: str | None = None,
    semantic_reason: str = "",
) -> TurnSignals:
    """Inspect one turn without mutating state or calling a model.

    The existing deterministic detectors are intentionally used as facts only;
    ``TurnPolicy`` decides whether any fact becomes an executable action.
    """
    text = user_text or ""
    interruption = bool(snapshot.active_scale) and is_scale_interruption_text(text)
    explicit_relaxation, explicit_relaxation_type = detect_explicit_relaxation_request(text)
    return TurnSignals(
        explicit_end_requested=is_user_explicit_end_text(text),
        active_scale_pause_requested=interruption,
        active_scale_refusal=interruption,
        explicit_relaxation_requested=explicit_relaxation,
        explicit_relaxation_type=explicit_relaxation_type,
        explicit_game_requested=_contains_any(text, _EXPLICIT_GAME_PHRASES),
        deterministic_scale_candidate=deterministic_scale_candidate,
        proactive_relaxation_candidate=proactive_relaxation_candidate,
        legacy_relaxation_candidate=legacy_relaxation_candidate,
        legacy_game_candidate=bool(legacy_game_candidate),
        semantic_ambiguity=bool(semantic_ambiguity),
        semantic_target=semantic_target,
        semantic_reason=semantic_reason,
    )
