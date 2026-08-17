"""Passive observations for transcript-critical semantic ambiguity.

This module never rewrites text, assigns a score, starts a scale, or chooses a
business action. It only identifies phrases that require deterministic
clarification before interpretation.
"""

from __future__ import annotations

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


_NEGATION_PATTERNS = ("中途部醒", "不睡不着")
_FREQUENCY_PATTERNS = ("说不准频率", "频率记不清", "频率我记不清", "频率不清楚", "不知道频率")
_DURATION_PATTERNS = ("不知道多久", "多长时间我也不清楚", "多久也说不清", "持续多久不清楚")
_QUANTITY_PATTERNS = ("几次记不清", "多少次记不清", "数量不清楚", "具体几次不知道")


def inspect_input_semantics(text: str) -> InputSemanticFlags:
    value = str(text or "").strip()
    negation = any(pattern in value for pattern in _NEGATION_PATTERNS)
    frequency = any(pattern in value for pattern in _FREQUENCY_PATTERNS)
    duration = any(pattern in value for pattern in _DURATION_PATTERNS)
    quantity = any(pattern in value for pattern in _QUANTITY_PATTERNS)
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
