"""Conversational strategy and stance: the two answers that steer a chat turn.

The intervention question decides *whether the interview changes task* (hold,
offer a scale, offer relaxation, clarify).  On the overwhelming majority of
turns the answer is "hold", and that answer used to be the entire contribution
of the decision layer: the writer was told to continue and nothing more.

These two questions answer what "continue" actually means:

``strategy``  which conversational move this turn should make -- open a new
              area, go one layer deeper, stay with the feeling, lower the
              demand, or close the topic.
``stance``    whether the turn should end in a question at all.

Both are multiple-choice on purpose.  The decision backend scores options; it
does not generate text.  Discrete answers are also checkable: a strategy with
no matching wording in the reply is a measurable miss, which is what makes
"did the decision layer do anything" answerable instead of a matter of taste.

Deliberately *not* modelled here:

* diagnosis, severity, or risk -- safety owns that, and a scored option must
  never be read as a clinical judgement;
* which scale to use -- that belongs to ``timing`` and the scale registry;
* any wording.  These labels pick a paragraph of guidance; the writer still
  produces the sentence.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

STRATEGY_LABELS: Tuple[str, ...] = (
    "explore",
    "deepen",
    "hold",
    "stabilize",
    "close",
)

STANCE_LABELS: Tuple[str, ...] = (
    "probe",
    "reflect",
    "summarize",
    "quiet",
)

#: Option text shown to the model.  The model reads a position letter, so the
#: text exists for the human reading a transcript, not for ranking quality.
STRATEGY_OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("Open an area the participant has not talked about yet", "explore"),
    ("Stay on the detail just mentioned and go one layer deeper", "deepen"),
    ("Stay with the feeling without moving the topic forward", "hold"),
    ("Lower the demand and repair the conversation", "stabilize"),
    ("Bring this topic to a close", "close"),
)

STANCE_OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("End the turn with one question the participant can answer", "probe"),
    ("Acknowledge what was said without asking anything", "reflect"),
    ("Gather what was said into one short summary, then stop", "summarize"),
)

STRATEGY_OPTION_TEXTS: Tuple[str, ...] = tuple(text for text, _ in STRATEGY_OPTIONS)
STANCE_OPTION_TEXTS: Tuple[str, ...] = tuple(text for text, _ in STANCE_OPTIONS)

STRATEGY_QUESTION_EN = (
    "Given the interview so far, what should this turn do conversationally? "
    "Choose the single move that helps the interview most right now."
)
STRATEGY_QUESTION_ZH = "结合目前的访谈进展，这一轮在对话上最该做哪一件事？只选一件。"

STANCE_QUESTION_EN = (
    "Should this turn end with a question for the participant, or should it "
    "only respond?"
)
STANCE_QUESTION_ZH = "这一轮应该以一个问题结束，还是只回应、不提问？"

# ---------------------------------------------------------------------------
# Focus: which dimension of the participant's experience this turn addresses
# ---------------------------------------------------------------------------
#
# Strategy says what kind of move to make; on its own it still leaves the writer
# free to ask about anything, so the interview can circle the same obvious topic
# ("睡得怎么样") all session.  Focus names the dimension, which makes the turn
# checkable: the reply either contains a concrete word from that dimension or it
# does not.
#
# Deliberately excluded: risk.  Self-harm is detected by rules and handled by
# policy; a scored option would read as "the model thinks this is a suicide
# turn", which is a clinical claim no option label may make.

FOCUS_LABELS: Tuple[str, ...] = (
    "sleep",
    "mood",
    "interest",
    "energy",
    "cognition",
    "appetite",
    "body",
    "daily",
)

#: Human-readable name for each dimension, used in prompts and reports.
FOCUS_NAMES_ZH: Mapping[str, str] = {
    "sleep": "睡眠",
    "mood": "情绪",
    "interest": "兴趣与愉悦",
    "energy": "精力与疲劳",
    "cognition": "注意力与做事",
    "appetite": "食欲与体重",
    "body": "身体不适",
    "daily": "日常生活与人际",
}

FOCUS_OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("Sleep: getting to sleep, waking at night, waking early, daytime tiredness", "sleep"),
    ("Mood: low mood, sadness, irritability, feeling numb or empty", "mood"),
    ("Interest and pleasure: things that used to be enjoyable", "interest"),
    ("Energy and fatigue: getting through the day, feeling worn out", "energy"),
    ("Attention and getting things done: focus, decisions, small tasks", "cognition"),
    ("Appetite and weight: eating more or less than usual", "appetite"),
    ("Bodily discomfort: headache, chest tightness, pain, tension", "body"),
    ("Daily life and people: work, study, family, being with others", "daily"),
)

FOCUS_OPTION_TEXTS: Tuple[str, ...] = tuple(text for text, _ in FOCUS_OPTIONS)

FOCUS_QUESTION_EN = (
    "Which single area of the participant's experience should this turn ask "
    "about? Choose the area that best fits what was just said.  If that area "
    "has already been asked about repeatedly this session, choose the closest "
    "area that has not been covered yet."
)
FOCUS_QUESTION_ZH = (
    "这一轮的问题最该落在来访者体验的哪一个方面？"
    "优先选和刚才那句话最贴合的方面；那个方面本次已经反复问过时，"
    "再选最接近、还没覆盖到的方面。"
)

_STRATEGY_BY_TEXT: Mapping[str, str] = {
    text.lower(): label for text, label in STRATEGY_OPTIONS
}
_STANCE_BY_TEXT: Mapping[str, str] = {
    text.lower(): label for text, label in STANCE_OPTIONS
}
_FOCUS_BY_TEXT: Mapping[str, str] = {
    text.lower(): label for text, label in FOCUS_OPTIONS
}


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def normalize_strategy(value: Any) -> Optional[str]:
    """Accept a label or its option text; return the label or ``None``.

    An unrecognised answer becomes ``None`` rather than an exception: a
    malformed auxiliary answer must never invalidate the primary decision.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower().replace("-", "_").replace(" ", "_")
    if lowered in STRATEGY_LABELS:
        return lowered
    return _STRATEGY_BY_TEXT.get(text.lower())


def normalize_stance(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower().replace("-", "_").replace(" ", "_")
    if lowered in STANCE_LABELS:
        return lowered
    return _STANCE_BY_TEXT.get(text.lower())


def normalize_focus(value: Any) -> Optional[str]:
    """Accept a label or its option text; return the label or ``None``.

    Some backends answer with the option text, some with an index letter, some
    with a free word ("睡眠").  An unrecognised answer becomes ``None``: a
    missing focus only costs a generic question, while a wrong one would steer
    the interview into a topic the participant never raised.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower().replace("-", "_").replace(" ", "_")
    if lowered in FOCUS_LABELS:
        return lowered
    mapped = _FOCUS_BY_TEXT.get(text.lower())
    if mapped:
        return mapped
    for label, name in FOCUS_NAMES_ZH.items():
        if text == name:
            return label
    return None


def focus_name(label: Optional[str]) -> str:
    """Chinese display name for a focus label; empty string for ``None``."""
    if not label:
        return ""
    return FOCUS_NAMES_ZH.get(label, label)


# ---------------------------------------------------------------------------
# Coherence
# ---------------------------------------------------------------------------

#: A turn that is meant to hold or close cannot end in a fresh question.
_QUESTION_FREE_STRATEGIES = frozenset({"hold", "close"})
_QUESTION_FREE_STANCES = frozenset({"reflect", "summarize", "quiet"})


def resolve_posture(
    strategy: Optional[str], stance: Optional[str]
) -> Tuple[Optional[str], Optional[str]]:
    """Make a strategy/stance pair coherent, or fall back to no posture.

    The two questions are answered independently, so they can contradict each
    other ("go deeper" + "do not ask anything").  An incoherent pair is worse
    than none: the writer receives two instructions that cancel out and drifts
    to its own default, which is exactly the behaviour these questions exist to
    replace.  Resolution is one-directional -- stance yields to strategy,
    because strategy is the larger decision.
    """
    strategy = normalize_strategy(strategy)
    stance = normalize_stance(stance)

    if stance == "quiet":
        # "Just answer briefly" outranks any move: there is nothing to steer.
        return None, stance
    if strategy in _QUESTION_FREE_STRATEGIES and stance == "probe":
        stance = "summarize" if strategy == "close" else "reflect"
    if strategy == "close" and stance == "reflect":
        # A close that only acknowledges leaves the topic hanging.
        stance = "summarize"
    if strategy in ("explore", "deepen") and stance in ("summarize", "reflect"):
        # Opening or deepening is a question by nature.  A summary would end
        # the topic the strategy is trying to start, and an acknowledgement
        # with no question has nothing to hold on to: measured on the 5-turn
        # fixed-text probe, those turns produced either a general truism
        # ("压力大的时候，人往往连呼吸都会变浅") or an empty completion.
        stance = "probe"
    if strategy is None and stance is None:
        return None, None
    return strategy, stance


def resolve_focus(
    strategy: Optional[str], stance: Optional[str], focus: Optional[str]
) -> Optional[str]:
    """Keep a focus only on a turn that asks something.

    The three questions are answered independently, so the model can name a
    dimension for a turn it also says should not ask a question.  A dimension
    with no question behind it is not a constraint the writer can satisfy -- it
    is a label that makes the reply look wrong for no reason -- so it is dropped
    instead of being passed down.

    Keeping it on an acknowledging ``deepen`` turn was tried and reverted: the
    writer had no question to spend the dimension on, so it filled the space
    with a truism about the dimension instead of a detail ("压力大的时候，人
    往往连呼吸都会变浅").  The coherence rule now removes that combination
    before it reaches here: an opening or deepening turn always asks.
    """
    focus = normalize_focus(focus)
    if focus is None:
        return None
    if stance == "quiet":
        return None
    if stance in ("reflect", "summarize"):
        return None
    if strategy in ("hold", "close", "stabilize"):
        return None
    return focus


__all__ = [
    "FOCUS_LABELS",
    "FOCUS_NAMES_ZH",
    "FOCUS_OPTIONS",
    "FOCUS_OPTION_TEXTS",
    "FOCUS_QUESTION_EN",
    "FOCUS_QUESTION_ZH",
    "STANCE_LABELS",
    "STANCE_OPTIONS",
    "STANCE_OPTION_TEXTS",
    "STANCE_QUESTION_EN",
    "STANCE_QUESTION_ZH",
    "STRATEGY_LABELS",
    "STRATEGY_OPTIONS",
    "STRATEGY_OPTION_TEXTS",
    "STRATEGY_QUESTION_EN",
    "STRATEGY_QUESTION_ZH",
    "focus_name",
    "normalize_focus",
    "normalize_stance",
    "normalize_strategy",
    "resolve_focus",
    "resolve_posture",
]
