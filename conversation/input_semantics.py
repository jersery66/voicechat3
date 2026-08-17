"""Passive observations for transcript-critical semantic ambiguity.

This module never rewrites text, assigns a score, starts a scale, or chooses a
business action. It only identifies phrases that require deterministic
clarification before interpretation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class InputSemanticFlags:
    negation_ambiguous: bool = False
    frequency_ambiguous: bool = False
    duration_ambiguous: bool = False
    quantity_ambiguous: bool = False
    symptom_polarity_ambiguous: bool = False
    semantic_target: str | None = None
    reason: str = ""

    @property
    def any_ambiguous(self) -> bool:
        return any(
            (
                self.negation_ambiguous,
                self.frequency_ambiguous,
                self.duration_ambiguous,
                self.quantity_ambiguous,
                self.symptom_polarity_ambiguous,
            )
        )


_NEGATION_PATTERNS = ("中途不醒", "中途部醒", "不睡不着")
_FREQUENCY_PATTERNS = ("大概两三天", "大概几天", "有时候吧", "偶尔吧", "说不准频率")
_DURATION_PATTERNS = ("不知道多久", "大概几周", "大概几个月", "多长时间不清楚")
_QUANTITY_RE = re.compile(r"(?:大概|差不多)?(?:两三|几|多少|[一二三四五六七八九十\d]+)(?:次|回|天|周|个月)")


def inspect_input_semantics(text: str) -> InputSemanticFlags:
    value = str(text or "").strip()
    negation = any(pattern in value for pattern in _NEGATION_PATTERNS)
    frequency = any(pattern in value for pattern in _FREQUENCY_PATTERNS)
    duration = any(pattern in value for pattern in _DURATION_PATTERNS)
    quantity = bool(_QUANTITY_RE.search(value))
    polarity = negation

    reasons: list[str] = []
    if negation:
        reasons.append("negation_or_sleep_polarity")
    if frequency:
        reasons.append("frequency")
    if duration:
        reasons.append("duration")
    if quantity:
        reasons.append("quantity")
    target = "night_waking" if negation else ("frequency" if frequency else None)
    return InputSemanticFlags(
        negation_ambiguous=negation,
        frequency_ambiguous=frequency,
        duration_ambiguous=duration,
        quantity_ambiguous=quantity,
        symptom_polarity_ambiguous=polarity,
        semantic_target=target,
        reason=";".join(reasons),
    )
