# adapters.factory — production wiring for adapter backends.
#
# Lazy imports on purpose: importing this module must never pull heavy
# dependencies (torch/funasr/pygame). Each builder imports its concrete
# service only when called, and returns the existing singleton so legacy
# code and the new engine share one instance per backend.
#
# Backend selection knobs will land here (e.g. VOICECHAT_TTS_BACKEND to
# choose VoxCPM vs CosyVoice) — today each slot has a single production
# implementation, matching legacy behavior exactly.

from typing import Optional


def build_llm_backend():
    from services.llm_service import get_llm_service
    return get_llm_service()


def build_agent_backend():
    from services.agent_service import get_agent_service
    return get_agent_service()


def build_rag_backend():
    from services.rag_service import get_rag_service
    return get_rag_service()


def build_tts_backend():
    """TTS backend selection point. Currently VoxCPM2 only (legacy:
    services/tts_service.py hard-imports the VoxCPM implementation)."""
    from services.tts_service import get_tts_service
    return get_tts_service()


def build_stt_backend():
    from services.stt_service import get_stt_service
    return get_stt_service()


def build_video_backend():
    from services.tools.video_tool import VideoPlayTool
    return VideoPlayTool()


def build_storage_backend(data_root: Optional[str] = None):
    from data.data_manager import get_data_manager
    return get_data_manager()


def build_report_backend():
    from services.report_service import get_report_service
    return get_report_service()
