# adapters.protocols — the stable interfaces the orchestration layer
# depends on. Every method set below was verified against the REAL call
# surface of services/pipeline.py and ui/main_window.py (independent
# review of be62001 confirmed completeness).
#
# Known debt (to clean during the authority switch):
#   - AgentBackend exposes `_keyword_crisis_risk` because the pipeline
#     still calls that private name directly. Promote it to a public
#     quick_crisis_check() when wiring authoritative mode.

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class LLMBackend(Protocol):
    """Streaming chat model used for the main counseling conversation."""

    conversation_history: List[Dict[str, str]]

    def chat(self, text: str, system_suffix: str = ""):
        """Yield response chunks (str). Must append the user message to
        conversation_history on start and the full assistant reply at the
        end (real LLMService parity)."""
        ...

    def reset_conversation(self) -> None: ...

    def set_history_context(self, context: str) -> None: ...


@runtime_checkable
class AgentBackend(Protocol):
    """Small routing model: intent/emotion/crisis/scale decisions."""

    def is_available(self) -> bool: ...

    def route_conversation_actions(
        self,
        user_text: str = "",
        recent_history: str = "",
        current_round: int = 0,
        active_scale: Optional[str] = None,
        collected_scales: Optional[dict] = None,
        relaxation_done: bool = False,
    ) -> Dict[str, Any]: ...

    def classify_intent(self, text: str) -> Dict[str, Any]: ...

    def detect_emotion(self, text: str) -> Dict[str, Any]: ...

    def assess_crisis_risk(self, text: str, use_llm: bool = True) -> Dict[str, Any]: ...

    # Debt: private name kept because pipeline calls it directly.
    def _keyword_crisis_risk(self, text: str) -> Dict[str, Any]: ...


@runtime_checkable
class RAGBackend(Protocol):
    """Knowledge-base retrieval producing system-prompt suffixes."""

    def get_system_suffix(self, query: str) -> str: ...


@runtime_checkable
class TTSBackend(Protocol):
    """Speech synthesis + playback."""

    def generate_and_play(self, text: str) -> None: ...

    def stop_playing(self) -> None: ...


@runtime_checkable
class STTBackend(Protocol):
    """Microphone capture + transcription."""

    def transcribe(self, audio_data: Any) -> str: ...

    def start_recording(self) -> None: ...

    def stop_recording(self) -> Any: ...


@runtime_checkable
class VideoBackend(Protocol):
    """Relaxation video playback tool (services/tools/video_tool surface)."""

    def execute(self, relaxation_type: str = "") -> Any: ...


@runtime_checkable
class StorageBackend(Protocol):
    """Session persistence: messages, audio, metadata."""

    def set_user_id(self, subject_id: Optional[str]) -> None: ...

    def save_user_message(self, audio: Any, text: str) -> Any: ...

    def save_assistant_message(self, audio: Any, text: str,
                               sample_rate: Optional[int] = None) -> Any: ...

    def start_new_session(self) -> Any: ...


@runtime_checkable
class ReportBackend(Protocol):
    """Round/time tracking consumed by the pipeline (report subset)."""

    def increment_round(self) -> None: ...

    def get_round_count(self) -> int: ...

    def should_warn_time_limit(self): ...

    def is_over_limit(self) -> bool: ...
