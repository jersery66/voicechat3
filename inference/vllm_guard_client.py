"""Optional Qwen3Guard adapter served through vLLM's OpenAI API."""

from __future__ import annotations

import re
from typing import Any

from openai import OpenAI

from safety.types import EvidenceSpan, SafetyAction, SafetyDecision


class VLLMGuardClient:
    """Map a Qwen3Guard prompt-classification response to ``SafetyDecision``.

    The deterministic crisis policy remains the primary safety boundary.  This
    client provides only an optional second opinion and never raises into the
    conversation path when its separately managed vLLM service is unavailable.
    """

    _SAFETY_LABEL = re.compile(r"Safety\s*:\s*(Safe|Unsafe|Controversial)", re.IGNORECASE)
    _CATEGORIES = re.compile(r"Categories?\s*:\s*(.+)", re.IGNORECASE)

    def __init__(self, *, model: str, base_url: str, api_key: str = "EMPTY",
                 timeout: float = 3.0) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=0,
        )

    @staticmethod
    def _field(value: Any, name: str, default: Any = None) -> Any:
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    @classmethod
    def _parse(cls, content: str) -> SafetyDecision:
        """Interpret the documented Qwen3Guard ``Safety/Categories`` format."""
        label_match = cls._SAFETY_LABEL.search(content or "")
        category_match = cls._CATEGORIES.search(content or "")
        label = label_match.group(1).lower() if label_match else ""
        categories = (
            [category.strip() for category in category_match.group(1).split(",") if category.strip()]
            if category_match else []
        )
        category_text = " ".join(categories).lower()
        self_harm = "suicide" in category_text or "self-harm" in category_text
        violence = "violent" in category_text

        if self_harm:
            action, risk = SafetyAction.MONITOR, 3
        elif violence:
            action, risk = SafetyAction.MONITOR, 2
        elif label == "unsafe":
            action, risk = SafetyAction.MONITOR, 1
        else:
            action, risk = SafetyAction.NONE, 0

        return SafetyDecision(
            self_harm_signal=self_harm,
            violence_signal=violence,
            uncertainty=not bool(label_match),
            evidence_spans=[EvidenceSpan(category="guard", text=category) for category in categories],
            action=action,
            risk_level=risk,
            source="guard_model",
        )

    def assess_input(self, text: str) -> SafetyDecision:
        """Classify a user turn, preserving deterministic safety on failure."""
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": text}],
                temperature=0,
                max_tokens=128,
            )
            choices = self._field(response, "choices", []) or []
            message = self._field(choices[0], "message") if choices else None
            content = str(self._field(message, "content", "") or "")
            return self._parse(content)
        except Exception:
            return SafetyDecision(uncertainty=True, source="guard_model")
