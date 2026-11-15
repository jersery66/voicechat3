"""Pure interpretation of one participant answer for an active scale item."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional

from core.scoring import (
    GAD7_POSITIVE_KEYWORDS_BY_ITEM,
    PHQ_POSITIVE_KEYWORDS_BY_ITEM,
    infer_scale_score_from_text,
    is_scale_interruption_text,
)
from services.scales import get_scale_manager


def _item_keywords(scale_name: str, item: int) -> list[str]:
    """Symptom keywords that identify one item, empty when none are defined."""
    if scale_name == "PHQ-9":
        return list(PHQ_POSITIVE_KEYWORDS_BY_ITEM.get(item, []))
    if scale_name == "GAD-7":
        return list(GAD7_POSITIVE_KEYWORDS_BY_ITEM.get(item, []))
    return []


def _has_item_keywords(scale_name: str, item: int) -> bool:
    return bool(_item_keywords(scale_name, item))


def _mentions_any_item(text: str, scale_name: str) -> bool:
    """True when the reply names any symptom from this scale at all."""
    if not text:
        return False
    if scale_name == "PHQ-9":
        table = PHQ_POSITIVE_KEYWORDS_BY_ITEM
    elif scale_name == "GAD-7":
        table = GAD7_POSITIVE_KEYWORDS_BY_ITEM
    else:
        return False
    return any(keyword in text for keywords in table.values() for keyword in keywords)


def _turn_is_about_item(
    answer_text: str,
    scale_name: str,
    item: int,
    question_text: str,
) -> bool:
    """True when either the answer or the question names this item's symptom.

    A bare "没有" names nothing, so on its own it can never establish that the
    participant is answering the active item.  The question we just spoke is
    the second witness: if it mentioned the symptom, the denial belongs here.
    """
    keywords = _item_keywords(scale_name, item)
    if not keywords:
        return True
    if any(kw in (answer_text or "") for kw in keywords):
        return True
    if question_text:
        return any(kw in question_text for kw in keywords)
    return False


@dataclass(frozen=True)
class ScaleAnswerInterpretation:
    """Structured candidate produced before Runtime state mutation."""

    status: str
    score: Optional[int]
    scale_name: Optional[str]
    item: Optional[int]
    reason: str = ""


class ScaleAnswerInterpreter:
    """Map clear answer phrases to candidates without touching ScaleRuntime."""

    _AMBIGUOUS_MARKERS = (
        "有时候",
        "有时",
        "还行",
        "可能",
        "差不多",
        "挺多",
        "不知道",
        "不清楚",
    )

    def __init__(self) -> None:
        self._manager = get_scale_manager()

    def interpret(
        self,
        text: str,
        *,
        scale_name: str,
        item: int,
        question_text: str = "",
    ) -> ScaleAnswerInterpretation:
        """Return a candidate/status; never call or mutate the Runtime.

        ``question_text`` is the reply the assistant just spoke.  It is the
        only evidence of what was actually asked, and a bare "没有" is scored
        against the active item only when it belongs to that item's question.
        """
        definition = self._manager.get_scale_definition(scale_name)
        if definition is None or not self._manager.validate_answer(
            scale_name, item=item, score=0
        ):
            return ScaleAnswerInterpretation(
                status="unmatched",
                score=None,
                scale_name=scale_name,
                item=item,
                reason="invalid_scale_or_item",
            )

        normalized = (text or "").strip()
        # A compound utterance may contain a valid answer before the request
        # to rest. Interpret that prefix first; an ambiguous prefix remains
        # unanswered and is handled as a pause below.
        answer_text = re.split(
            r"(?:先让我|让我|先|我想|我要)?(?:休息|暂停)",
            normalized,
            maxsplit=1,
        )[0]
        answer_text = answer_text.strip(" ，,。！？!?；;") or normalized

        if not answer_text or any(marker in answer_text for marker in self._AMBIGUOUS_MARKERS):
            return ScaleAnswerInterpretation(
                status="ambiguous",
                score=None,
                scale_name=scale_name,
                item=item,
                reason="clarification_required",
            )

        score = infer_scale_score_from_text(
            answer_text, scale_name, item=item, question_text=question_text
        )
        if score is None and scale_name in ("PHQ-9", "GAD-7"):
            # A standalone frequency answer is only sufficient when the
            # question really was about this item.  Without that check the
            # fallback turns every "没有" in the conversation -- including
            # "没有" answering "有没有能让自己静下来的事" -- into Q1 = 0.
            #
            # When no question is supplied there is no evidence either way, so
            # the historical contract stands: score from the answer alone.
            # Dropping the answer instead would silently leave items pending
            # wherever the caller has no transcript (first turn, replay
            # harness, truncated history).
            # When the question names no scale symptom at all there is no
            # topical evidence either way -- a fallback reply like "我在听着"
            # neither asks about Q1 nor about something else.  Keep the
            # historical contract there instead of dropping a legitimate
            # answer; the pipeline's waiting-turn guard handles the case where
            # the conversation genuinely moved on.
            if not question_text or not _mentions_any_item(question_text, scale_name):
                topical = True
            else:
                topical = not _has_item_keywords(scale_name, item) or _turn_is_about_item(
                    answer_text, scale_name, item, question_text
                )
            if topical and (
                answer_text in {"没有", "没", "完全不会", "不会"}
                or any(
                    k in answer_text
                    for k in ("完全没有", "从来没有", "一点没有", "几乎没有", "几乎不")
                )
            ):
                score = 0
            elif topical and any(k in answer_text for k in ("几乎每天", "每一天", "每天", "天天")):
                score = 3
            elif topical and any(k in answer_text for k in ("一半以上", "大多数", "超过一半", "多半", "大部分")):
                score = 2
            elif topical and any(k in answer_text for k in ("一半", "经常", "时常", "常常")):
                # "一半吧" is how participants actually answer a frequency
                # probe; without this the item stayed pending for ten turns.
                score = 2
            elif topical and any(k in answer_text for k in ("几天", "偶尔")):
                score = 1
            elif topical and any(k in answer_text for k in ("很少", "难得")):
                score = 1
            elif topical and any(
                k in answer_text
                for k in ("一直", "总是", "老是", "基本每天", "一直都会")
            ):
                # "一直都会" answers "现在也提不起劲吗？" -- a duration answer
                # that used to be dropped entirely because it names no symptom
                # keyword, leaving Q1 pending and the score at zero.
                score = 3
        if score is not None and self._manager.validate_answer(
            scale_name, item=item, score=score
        ):
            return ScaleAnswerInterpretation(
                status="accepted",
                score=score,
                scale_name=scale_name,
                item=item,
                reason="definition_backed_phrase",
            )

        if is_scale_interruption_text(normalized):
            return ScaleAnswerInterpretation(
                status="pause",
                score=None,
                scale_name=scale_name,
                item=item,
                reason="participant_interruption",
            )

        return ScaleAnswerInterpretation(
            status="unmatched",
            score=None,
            scale_name=scale_name,
            item=item,
            reason="no_definition_backed_mapping",
        )
