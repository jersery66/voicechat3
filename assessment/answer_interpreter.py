"""Pure interpretation of one participant answer for an active scale item."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional

from core.scoring import infer_scale_score_from_text, is_scale_interruption_text
from services.scales import get_scale_manager


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
    ) -> ScaleAnswerInterpretation:
        """Return a candidate/status; never call or mutate the Runtime."""
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

        score = infer_scale_score_from_text(answer_text, scale_name, item=item)
        if score is None and scale_name in ("PHQ-9", "GAD-7"):
            # The current scale question supplies the symptom context, so a
            # standalone clear frequency answer is sufficient here.
            if answer_text in {"没有", "没", "完全不会", "不会"}:
                score = 0
            elif "几乎每天" in answer_text or "每天" in answer_text or "天天" in answer_text:
                score = 3
            elif "一半以上" in answer_text or "大多数" in answer_text or "超过一半" in answer_text:
                score = 2
            elif "几天" in answer_text or "偶尔" in answer_text:
                score = 1
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
