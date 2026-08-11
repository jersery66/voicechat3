# adapters.protocols — the stable interfaces the orchestration layer
# depends on.
#
# SCOPE: the method sets below cover the call surface of
# services/pipeline.py (complete, verified by independent review) PLUS the
# lifecycle-critical methods used by ui/main_window.py that the engine
# will need when it becomes authoritative (start_session, greeting
# generators, is_vad_triggered, FILE_MAP). Remaining main_window-only
# helpers (warmup/cleanup/profile persistence etc.) are intentionally NOT
# in scope yet; they join when their owning flow migrates.
#
# Signature parity with the real services is a review-checked invariant
# (see tests/test_adapter_conformance.py and the Phase-2 review):
# parameter names/defaults below mirror llm_service/agent_service/
# stt_service/data_manager/rag_service/video_tool exactly.
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

    def chat(self, user_message: str, system_suffix: Optional[str] = None):
        """Yield response chunks (str). Must append the user message to
        conversation_history on start and the full assistant reply at the
        end (real LLMService parity)."""
        ...

    def reset_conversation(self, clear_context: bool = False) -> None: ...

    def set_history_context(self, context: str) -> None: ...


@runtime_checkable
class AgentBackend(Protocol):
    """Small routing model: intent/emotion/crisis/scale decisions plus
    lifecycle greeting generation."""

    def is_available(self) -> bool: ...

    def route_conversation_actions(
        self,
        user_text: str,
        recent_history: str = "",
        current_round: int = 0,
        active_scale: Optional[str] = None,
        collected_scales: Optional[dict] = None,
        relaxation_done: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]: ...

    def classify_intent(self, text: str, timeout: Optional[float] = None) -> Dict[str, Any]: ...

    def detect_emotion(self, text: str) -> Dict[str, Any]: ...

    def assess_crisis_risk(self, text: str, timeout: Optional[float] = None,
                           use_llm: bool = True) -> Dict[str, Any]: ...

    # Lifecycle greeting generation (main_window parity)
    def generate_greeting(self, *args, **kwargs) -> str: ...

    def generate_post_relaxation_greeting(self, *args, **kwargs) -> str: ...

    def generate_fill_info_prompt(self, *args, **kwargs) -> str: ...

    # Debt: private name kept because pipeline calls it directly.
    def _keyword_crisis_risk(self, text: str) -> Dict[str, Any]: ...


@runtime_checkable
class RAGBackend(Protocol):
    """Knowledge-base retrieval producing system-prompt suffixes."""

    def get_system_suffix(self, user_text: str) -> Optional[str]: ...


@runtime_checkable
class TTSBackend(Protocol):
    """Speech synthesis + playback."""

    def generate_and_play(self, text: str) -> None: ...

    def stop_playing(self) -> None: ...


@runtime_checkable
class STTBackend(Protocol):
    """Microphone capture + transcription."""

    def transcribe(self, audio: Any) -> str: ...

    def start_recording(self) -> None: ...

    def stop_recording(self) -> Any: ...

    def is_vad_triggered(self) -> bool: ...


@runtime_checkable
class VideoBackend(Protocol):
    """Relaxation video playback tool (services/tools/video_tool surface)."""

    FILE_MAP: Dict[str, str]

    def execute(self, relaxation_type: Optional[str] = None,
                filename: Optional[str] = None) -> Any: ...


@runtime_checkable
class StorageBackend(Protocol):
    """Session persistence: messages, audio, metadata."""

    def set_user_id(self, user_id: Optional[str]) -> None: ...

    def save_user_message(self, audio: Any, text: str) -> Any: ...

    def save_assistant_message(self, audio: Any, text: str,
                               sample_rate: int = 48000) -> Any: ...

    def start_new_session(self) -> Any: ...


@runtime_checkable
class ReportBackend(Protocol):
    """Round/time tracking consumed by the pipeline (report subset).

    start_session is included because the engine must be able to reset
    round/time flags across subjects (main_window calls it at session
    start)."""

    def start_session(self) -> None: ...

    def increment_round(self) -> None: ...

    def get_round_count(self) -> int: ...

    def should_warn_time_limit(self): ...

    def is_over_limit(self) -> bool: ...
