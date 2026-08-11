# adapters.factory — production wiring for adapter backends.
#
# Lazy imports on purpose: importing this module must never pull heavy
# dependencies (torch/funasr/pygame). Each builder imports its concrete
# service only when called.
#
# INSTANCE SHARING — honest status (verified by review):
#   - agent / rag builders return module singletons that MainWindow ALSO
#     obtains via the same getters -> shared instance.
#   - llm / stt / tts / storage / report: legacy MainWindow constructs its
#     own instances directly (load_models), so these getters currently
#     return DIFFERENT objects than the legacy ones. Before authoritative
#     engine wiring, MainWindow must be switched to these builders so both
#     sides share one instance per backend (otherwise: two conversation
#     histories, two session dirs, two round counters).
#
# Backend selection knobs will land here (e.g. VOICECHAT_TTS_BACKEND to
# choose VoxCPM vs CosyVoice) — today each slot has a single production
# implementation, matching legacy behavior exactly.

import os


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


def build_video_backend(base_dir: str = None):
    """VideoPlayTool requires base_dir (legacy passes the app directory)."""
    if base_dir is None:
        from config import APP_ROOT
        base_dir = APP_ROOT
    from services.tools.video_tool import VideoPlayTool
    return VideoPlayTool(base_dir)


def build_storage_backend():
    """DataManager uses config.DATA_ROOT; the singleton takes no root
    argument (a per-root instance would silently diverge from legacy)."""
    from data.data_manager import get_data_manager
    return get_data_manager()


def build_report_backend():
    from services.report_service import get_report_service
    return get_report_service()
