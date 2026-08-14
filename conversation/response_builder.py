"""Provider-neutral normalization of generated language.

The live contract is plain generated text -> spoken/TTS text.  The optional
``analysis_text`` value is retained only for old tagged transcripts and never
feeds a decision or state transition.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.tags import clean_for_display, clean_for_tts, split_legacy_response


@dataclass(frozen=True)
class BuiltResponse:
    generated_text: str
    spoken_text: str
    tts_text: str
    # Compatibility/reporting projection only; not a live protocol field.
    analysis_text: str = ""


class ResponseBuilder:
    """Normalize provider text without interpreting business actions."""

    @staticmethod
    def build(raw_response: str) -> BuiltResponse:
        generated = str(raw_response or "").strip()
        analysis, spoken = split_legacy_response(generated)
        return BuiltResponse(
            generated_text=generated,
            spoken_text=clean_for_display(spoken),
            tts_text=clean_for_tts(spoken),
            analysis_text=analysis,
        )
