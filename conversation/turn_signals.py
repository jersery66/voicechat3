"""Pure observations used by :mod:`conversation.turn_policy`."""

from __future__ import annotations

from conversation.contracts import TurnSignals, TurnStateSnapshot
from core.scoring import is_scale_interruption_text, is_user_explicit_end_text

__all__ = ["TurnSignals", "collect_turn_signals"]


_EXPLICIT_GAME_PHRASES = (
    "想玩游戏",
    "想玩个游戏",
    "玩个游戏",
    "玩游戏",
    "开始游戏",
    "来个小游戏",
    "玩一局",
)

_RELAXATION_REQUEST_MARKERS = (
    "我想要",
    "我想",
    "我要",
    "想做",
    "想要",
    "要做",
    "来个",
    "可以做",
    "可以来",
    "能不能做",
    "能不能来",
    "试试",
    "试一下",
    "做个",
    "做一下",
    "先",
    "帮我做",
    "帮我来",
)
_RELAXATION_MENTION_MARKERS = (
    "以前做过",
    "曾经做过",
    "过去做过",
    "之前试过",
    "以前试过",
    "曾经试过",
    "过去试过",
    "让我做过",
    "教过我",
    "做过",
    "试过",
    "对我没用",
    "对我没什么用",
    "没有用",
    "没什么用",
    "不管用",
    "没效果",
)
_STANDALONE_RELAXATION_COMMANDS = {
    "呼吸练习": "breathing",
    "呼吸训练": "breathing",
    "呼吸放松": "breathing",
    "肌肉放松": "muscle",
    "冥想": "meditation",
    "冥想练习": "meditation",
    "正念": "meditation",
    "正念练习": "meditation",
    "想放松": None,
    "放松一下": None,
    "放松训练": None,
    "做个放松": None,
    "做放松训练": None,
    "做冥想": "meditation",
    "休息一下": None,
    "暂停一下": None,
    "先休息一下": None,
    "先暂停一下": None,
}
_GENERIC_RELAXATION_PHRASES = ("放松", "休息", "暂停", "呼吸", "肌肉", "冥想", "正念")
_TYPED_RELAXATION_PHRASES = (
    ("breathing", ("呼吸练习", "呼吸训练", "呼吸放松", "深呼吸", "做个呼吸")),
    ("muscle", ("肌肉放松", "渐进性肌肉放松", "做肌肉放松")),
    ("meditation", ("冥想练习", "正念练习", "冥想", "正念")),
)


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    normalized = (text or "").strip().lower()
    return any(phrase in normalized for phrase in phrases)


def _has_request_marker_before(text: str, phrase: str) -> bool:
    """Recognize a short request marker immediately before a target phrase."""
    start = text.find(phrase)
    while start >= 0:
        prefix = text[max(0, start - 12):start]
        if any(marker in prefix for marker in _RELAXATION_REQUEST_MARKERS):
            return True
        start = text.find(phrase, start + 1)
    return False


def detect_explicit_relaxation_request(text: str) -> tuple[bool, str | None]:
    """Return a request/type pair while separating requests from mentions.

    Typed and generic phrases only count when a short request marker occurs
    immediately before them.  Exact standalone commands remain supported;
    otherwise historical/evaluative mentions are passive observations.
    """
    normalized = (text or "").strip().lower()
    if not normalized:
        return False, None

    standalone_type = _STANDALONE_RELAXATION_COMMANDS.get(normalized)
    if normalized in _STANDALONE_RELAXATION_COMMANDS:
        return True, standalone_type

    for relaxation_type, phrases in _TYPED_RELAXATION_PHRASES:
        if any(_has_request_marker_before(normalized, phrase) for phrase in phrases):
            return True, relaxation_type

    if any(_has_request_marker_before(normalized, phrase) for phrase in _GENERIC_RELAXATION_PHRASES):
        return True, None

    # A phrase without a request marker is a historical/evaluative mention or
    # an ordinary discussion of a technique, never an active intervention
    # request.  Keep this explicit for readability and future pattern audits.
    if _contains_any(normalized, _RELAXATION_MENTION_MARKERS):
        return False, None
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
