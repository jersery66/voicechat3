"""Pure observations used by :mod:`conversation.turn_policy`."""

from __future__ import annotations

from typing import Optional

from conversation.contracts import TurnSignals, TurnStateSnapshot
from core.scoring import is_scale_interruption_text, is_user_explicit_end_text

__all__ = [
    "TurnSignals",
    "collect_turn_signals",
    "detect_explicit_scale_request",
    "detect_offer_answer",
    "detect_risk_marker",
]


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

# An explicit request for an assessment is a user initiative, not a model
# judgement: once the participant asks, the policy may start the next planned
# instrument instead of asking "would you like to?" back at them.
_SCALE_REQUEST_PHRASES = (
    "做个量表",
    "做一下量表",
    "做量表",
    "量表",
    "问卷",
    "测评",
    "评估一下",
    "做个评估",
    "测一下",
    "测试一下",
    "填个表",
    "填问卷",
)
#: Negations that cancel a neighbouring request.
#:
#: Single-character markers are deliberately absent: a bare "别" would match
#: inside "特别" and silently cancel every request that happens to sit after
#: "我现在特别烦……".  Only unambiguous multi-character forms are listed.
_NEGATION_MARKERS = (
    "不想", "不用", "不要", "不做", "算了", "没必要", "拒绝",
    "没做过", "从来没", "以前没", "没测过",
    "别做", "别来", "别弄", "别测", "别问", "别提", "别给我",
)

#: Stance markers turn a request into a remark.
#:
#: "我知道可以放松，但我现在更想把这些说出来" is a participant declining an
#: activity while staying in the interview.  The characters "可以…放松" would
#: otherwise read as a request and the system would interrupt someone who just
#: said they want to keep talking.
_MENTION_STANCE_MARKERS = (
    "我知道", "我了解", "听说", "据说", "别人说", "医生说", "网上说",
    "大家都说", "好像", "是不是应该",
)
_SCALE_REQUEST_MARKERS = (
    "想", "要", "可以", "能不能", "帮我", "麻烦", "愿意", "做", "测", "填",
)

# --- answers to an outstanding invitation ----------------------------------
#
# These lists are only consulted while an offer is actually pending, so an
# unrelated "好" in ordinary conversation can never be read as consent.
_ACCEPT_EXACT = frozenset({
    "好", "好啊", "好呀", "好的", "好的呀", "可以", "可以啊", "可以呀",
    "行", "行啊", "行吧", "愿意", "愿意啊", "没问题", "当然", "对", "是的",
    "做", "做吧", "来吧", "试试", "试试吧", "要", "我要", "想", "想啊",
})
_ACCEPT_PHRASES = (
    "可以", "愿意", "没问题", "好的", "好啊", "行吧", "做吧", "来吧",
    "试试", "那就做", "那就来", "我们做", "我做", "做一下", "来一次",
)
_DECLINE_EXACT = frozenset({
    "不", "不了", "不用", "不要", "算了", "不想", "拒绝", "不要了",
    "不用了", "不想做", "不做了", "没必要", "没兴趣", "暂时不", "先不",
})
_DECLINE_PHRASES = (
    "不想", "不用", "不要", "算了", "没必要", "没兴趣", "不感兴",
    "暂时不", "先不", "拒绝", "不想做", "不做了",
)

#: Postponements.  They answer "not now", never "never": the invitation stays
#: open so the participant can come back to it two or three turns later.  These
#: used to live in the decline list, which closed the invitation on a phrase
#: that explicitly keeps it alive.
_DEFER_PHRASES = (
    "等一下", "等一会", "等会儿", "过一会", "待会", "待会儿", "一会儿",
    "晚点", "下次", "以后再说", "回头再说", "改天", "再说吧", "先放一放",
    "现在不要", "现在不行",
)

#: "不是不想做" is an explicit refusal *of the refusal*.  Stripped before the
#: decline check so a double negative is not read as declining.
_DOUBLE_NEGATION = (
    "不是不想", "并非不想", "不是不", "我没说不想", "没说不想", "不是不做",
)

#: Asking what an invitation involves.  Neither consent nor refusal, so the
#: invitation is kept and the participant gets an answer.
_OFFER_QUESTION_MARKERS = (
    "是什么", "做什么", "干什么", "多久", "多长时间", "要多少", "怎么",
    "流程", "哪些", "哪个", "什么量表", "解释", "说说", "说一下", "讲讲",
    "大概", "有没有", "会不会", "干什么用", "有什么用", "难不难",
)
_DEFER_EXACT = frozenset({
    "嗯", "嗯嗯", "哦", "随便", "看情况", "不知道", "都行", "再说", "再说吧",
})
#: Utterances that look like agreement but continue the interview instead of
#: answering the invitation.  "好，那我们继续聊" must not start a scale.
_CONTINUE_HINTS = (
    "继续聊", "接着说", "继续说", "还是聊", "先聊", "继续吧", "我们聊",
    "继续谈", "再聊聊",
)

#: Explicit statements that the participant wants to keep talking / be heard.
#:
#: Narrow on purpose: this suppresses an *invitation* (an interruption), so it
#: only fires on phrases that ask for the floor.  A general "sounds anxious"
#: guess must never be turned into "does not want an intervention".
_WANTS_TO_TALK_PHRASES = (
    "让我说完",
    "先听我说",
    "听我说完",
    "我想说说",
    "我想聊聊",
    "我想讲",
    "我还想说",
    "我还没说完",
    "继续聊",
    "接着聊",
    "先聊",
    "想把这些说出来",
)

#: How many leading characters may answer an invitation.
_ANSWER_HEAD_CHARS = 8

#: A turn no longer than this can plausibly *be* an answer.  Longer utterances
#: are the interview continuing, and asking "did you mean yes or no?" about a
#: paragraph would interrupt someone who never addressed the invitation.
_SHORT_ANSWER_CHARS = 8

#: Refusal fragments safe to match inside the opening of an utterance.
#:
#: Only multi-character forms qualify.  ``_DECLINE_EXACT`` also contains bare
#: "不" / "想" / "要", and substring-matching those would read "坐着也不舒服"
#: as a refusal of the questionnaire that was just offered.
_DECLINE_HEAD_PHRASES = tuple(
    phrase
    for phrase in tuple(_DECLINE_PHRASES) + tuple(_DECLINE_EXACT)
    if len(phrase) >= 2
)

#: A caveat inside an agreeing turn means the consent is unclear.
_ANSWER_CONTRAST_MARKERS = (
    "但", "不过", "可是", "然而", "其实", "只是", "除了",
    "我知道", "我觉得", "听说",
)

#: Safety markers owned by the risk path, not by this decision layer.  They
#: exist here only so an activity invitation can never be offered on a turn
#: that contains them.
_RISK_MARKERS = (
    "不想活", "想死", "去死", "自杀", "自残", "伤害自己", "活不下去",
    "结束生命", "轻生", "死了算了", "没有活着的意义", "活着没意思",
)


def _strip_punct(text: str) -> str:
    return (text or "").strip("。！？!?，,.、~～ \t\r\n")


def detect_risk_marker(text: str) -> bool:
    """True when the utterance carries a self-harm or suicide marker.

    This is a *routing* observation only.  It never decides anything; it stops
    the decision layer from trading a safety situation for an activity.
    """
    normalized = _strip_punct(text).lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in _RISK_MARKERS)


def detect_wants_to_talk(text: str) -> bool:
    """True when the participant explicitly asks for the floor.

    Used as a suppression signal for *invitations* only.  An invitation
    interrupts; someone who just said "let me finish" should not be handed a
    questionnaire or an exercise in the same turn.
    """
    normalized = _strip_punct(text).lower()
    if not normalized:
        return False
    return any(phrase in normalized for phrase in _WANTS_TO_TALK_PHRASES)


def detect_offer_question(text: str) -> bool:
    """True when the turn asks about an invitation instead of answering it.

    "可以先说一下是什么吗？" and "哪个量表？" are engagement: the participant
    is deciding.  Reading either as consent would be fabrication, and reading
    either as refusal would close a door they are still looking through.
    """
    normalized = _strip_punct(text).lower()
    if not normalized:
        return False
    if "？" in (text or "") or "?" in (text or ""):
        return True
    return any(marker in normalized for marker in _OFFER_QUESTION_MARKERS)


def detect_offer_deferral(text: str) -> bool:
    """True when the participant explicitly postpones an open invitation.

    Distinguished from a vague "嗯": postponement is an answer the system can
    act on (keep the offer, continue the interview), while vagueness needs a
    clarifying question.
    """
    normalized = _strip_punct(text).lower()
    if not normalized:
        return False
    for marker in _DOUBLE_NEGATION:
        normalized = normalized.replace(marker, "")
    return any(phrase in normalized for phrase in _DEFER_PHRASES)


def detect_offer_answer(text: str) -> str:
    """Classify an utterance as ``accept`` / ``decline`` / ``defer`` / ``none``.

    Only meaningful when an invitation is pending; the caller is responsible
    for that gate.  Deliberately conservative: an unclear answer returns
    ``defer`` (the invitation stays open) rather than being rounded up to
    consent.
    """
    normalized = _strip_punct(text).lower()
    if not normalized:
        return "defer"

    for marker in _DOUBLE_NEGATION:
        normalized = normalized.replace(marker, "")
    if not normalized:
        return "defer"

    if normalized in _DECLINE_EXACT:
        return "decline"
    if normalized in _DEFER_EXACT:
        return "defer"

    # Postponement outranks refusal: "现在不要，待会再说" says "not now", and the
    # invitation must survive it.
    if any(phrase in normalized for phrase in _DEFER_PHRASES):
        return "defer"

    # Only the opening of the utterance may answer an invitation.
    #
    # Substring matching over the whole sentence fabricates consent: while a
    # scale was pending, "我知道可以放松，但我现在更想把这些说出来" matched the
    # accept phrase "可以" and started an assessment the participant had just
    # declined.  An answer is short and it comes first, so only the head counts.
    head = normalized[:_ANSWER_HEAD_CHARS]
    if any(phrase in head for phrase in _DECLINE_HEAD_PHRASES):
        return "decline"

    # "好，那我们继续聊" is agreement to keep talking, not consent to a task.
    if any(hint in normalized for hint in _CONTINUE_HINTS):
        return "none"

    # A question back ("可以吗？") is not consent, so it is checked before the
    # accept phrases rather than after them.
    if "？" in (text or "") or "?" in (text or ""):
        return "defer"

    accepts = normalized in _ACCEPT_EXACT or any(phrase in head for phrase in _ACCEPT_PHRASES)
    if accepts:
        # A caveat anywhere in the turn makes the consent unclear: ask, do not
        # assume.  Deferring keeps the invitation open instead of discarding it.
        if any(marker in normalized for marker in _ANSWER_CONTRAST_MARKERS):
            return "defer"
        return "accept"

    return "none"


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    normalized = (text or "").strip().lower()
    return any(phrase in normalized for phrase in phrases)


def _has_request_marker_before(
    text: str,
    phrase: str,
    markers: tuple[str, ...] = _RELAXATION_REQUEST_MARKERS,
) -> bool:
    """Recognize a short request marker immediately before a target phrase.

    A negation inside that window cancels the request: "不想做量表" must never
    be read as asking for one, even though it contains the characters "想做".
    """
    start = text.find(phrase)
    while start >= 0:
        prefix = text[max(0, start - 12):start]
        if any(marker in prefix for marker in _NEGATION_MARKERS):
            start = text.find(phrase, start + 1)
            continue
        if any(marker in prefix for marker in _MENTION_STANCE_MARKERS):
            start = text.find(phrase, start + 1)
            continue
        if any(marker in prefix for marker in markers):
            return True
        start = text.find(phrase, start + 1)
    return False


def detect_explicit_scale_request(text: str) -> bool:
    """True when the participant themselves asks for an assessment."""
    normalized = (text or "").strip().lower()
    if not normalized:
        return False
    return any(
        _has_request_marker_before(normalized, phrase, _SCALE_REQUEST_MARKERS)
        for phrase in _SCALE_REQUEST_PHRASES
    )


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

    The offer answer is evaluated *only* when ``snapshot.pending_offer`` says an
    invitation is outstanding.  That gate is what stops ordinary agreement in
    the middle of an interview from being read as consent to a questionnaire.
    """
    text = user_text or ""
    interruption = bool(snapshot.active_scale) and is_scale_interruption_text(text)
    explicit_relaxation, explicit_relaxation_type = detect_explicit_relaxation_request(text)
    offer_accepted = offer_declined = offer_deferred = offer_question = False
    pending: Optional[str] = snapshot.pending_offer
    if pending:
        answer = detect_offer_answer(text)
        offer_accepted = answer == "accept"
        offer_declined = answer == "decline"
        offer_question = detect_offer_question(text) and not offer_declined
        # Explicit postponement keeps the invitation open without asking the
        # participant to repeat themselves.  Anything else unclear ("嗯",
        # "随便") is ambiguity, and ambiguity is clarified, never assumed.
        offer_deferred = (
            not offer_question and detect_offer_deferral(text)
        )
    return TurnSignals(
        explicit_end_requested=is_user_explicit_end_text(text),
        active_scale_pause_requested=interruption,
        active_scale_refusal=interruption,
        explicit_relaxation_requested=explicit_relaxation,
        explicit_relaxation_type=explicit_relaxation_type,
        explicit_scale_requested=detect_explicit_scale_request(text),
        explicit_game_requested=_contains_any(text, _EXPLICIT_GAME_PHRASES),
        deterministic_scale_candidate=deterministic_scale_candidate,
        proactive_relaxation_candidate=proactive_relaxation_candidate,
        legacy_relaxation_candidate=legacy_relaxation_candidate,
        legacy_game_candidate=bool(legacy_game_candidate),
        semantic_ambiguity=bool(semantic_ambiguity),
        semantic_target=semantic_target,
        semantic_reason=semantic_reason,
        offer_accepted=offer_accepted,
        offer_declined=offer_declined,
        offer_question=offer_question,
        offer_deferred=offer_deferred,
        offer_answer_ambiguous=bool(
            pending
            and not offer_accepted
            and not offer_declined
            and not offer_question
            and not offer_deferred
            # Only a short turn can be an answer at all.  A paragraph about
            # work is the interview continuing; treating it as an unclear
            # answer would interrupt someone who never addressed the offer.
            and len(_strip_punct(text)) <= _SHORT_ANSWER_CHARS
        ),
        risk_marker=detect_risk_marker(text),
        participant_wants_to_talk=detect_wants_to_talk(text),
    )
