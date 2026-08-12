"""Provider-neutral transformation of model output into spoken content."""

from __future__ import annotations

from dataclasses import dataclass

from services.pipeline import clean_for_display, clean_for_tts


@dataclass(frozen=True)
class BuiltResponse:
    analysis_text: str
    spoken_text: str
    tts_text: str


class ResponseBuilder:
    """Centralizes the existing delimiter and tag-leak compatibility rules."""

    @staticmethod
    def build(raw_response: str) -> BuiltResponse:
        analysis, spoken = ResponseBuilder._split_response(raw_response)
        return BuiltResponse(
            analysis_text=analysis,
            spoken_text=clean_for_display(spoken),
            tts_text=clean_for_tts(spoken),
        )

    @staticmethod
    def _split_response(raw_response: str) -> tuple[str, str]:
        if "|||" not in raw_response:
            return "", raw_response.strip()
        left, right = (part.strip() for part in raw_response.split("|||", 1))
        # Control tags are valid at the end of spoken text and must be
        # cleaned later; only the private analysis headings determine order.
        analysis_markers = ("【情绪识别】", "【状态评估】", "【变革话语】", "【策略选择】")
        left_is_analysis = any(marker in left for marker in analysis_markers)
        right_is_analysis = any(marker in right for marker in analysis_markers)
        if right_is_analysis and not left_is_analysis:
            return right, left
        return left, right
