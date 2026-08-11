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
#
# M34: every builder now degrades to None on missing dependencies (instead of
# raising ImportError), and build() is the single dispatch entry point.

import logging
import os

logger = logging.getLogger(__name__)


def build_llm_backend():
    try:
        from services.llm_service import get_llm_service
        return get_llm_service()
    except Exception as e:  # missing heavy deps (torch etc.)
        logger.warning(f"build_llm_backend failed: {e}")
        return None


def build_agent_backend():
    try:
        from services.agent_service import get_agent_service
        return get_agent_service()
    except Exception as e:
        logger.warning(f"build_agent_backend failed: {e}")
        return None


def build_rag_backend():
    try:
        from services.rag_service import get_rag_service
        return get_rag_service()
    except Exception as e:
        logger.warning(f"build_rag_backend failed: {e}")
        return None


def build_tts_backend():
    """TTS backend selection point. Currently VoxCPM2 only (legacy:
    services/tts_service.py hard-imports the VoxCPM implementation)."""
    try:
        from services.tts_service import get_tts_service
        return get_tts_service()
    except Exception as e:
        logger.warning(f"build_tts_backend failed: {e}")
        return None


def build_stt_backend():
    try:
        from services.stt_service import get_stt_service
        return get_stt_service()
    except Exception as e:
        logger.warning(f"build_stt_backend failed: {e}")
        return None


def build_video_backend(base_dir: str = None):
    """VideoPlayTool requires base_dir (legacy passes the app directory)."""
    if base_dir is None:
        from config import APP_ROOT
        base_dir = APP_ROOT
    try:
        from services.tools.video_tool import VideoPlayTool
        return VideoPlayTool(base_dir)
    except Exception as e:
        logger.warning(f"build_video_backend failed: {e}")
        return None


def build_storage_backend():
    """DataManager uses config.DATA_ROOT; the singleton takes no root
    argument (a per-root instance would silently diverge from legacy)."""
    try:
        from data.data_manager import get_data_manager
        return get_data_manager()
    except Exception as e:
        logger.warning(f"build_storage_backend failed: {e}")
        return None


def build_report_backend():
    try:
        from services.report_service import get_report_service
        return get_report_service()
    except Exception as e:
        logger.warning(f"build_report_backend failed: {e}")
        return None


def build(backend_type: str, **cfg):
    """Production backend dispatcher (minimal skeleton).

    TODO: when multiple implementations exist for a slot (e.g.
    VOICECHAT_TTS_BACKEND -> VoxCPM vs CosyVoice), select by configured knob
    here. Each builder degrades to None on missing dependencies rather than
    raising ImportError, so callers must handle a None return.

    Args:
        backend_type: one of llm/agent/rag/tts/stt/video/storage/report.
        cfg: backend-specific options (currently only ``base_dir`` for video).
    """
    builders = {
        "llm": build_llm_backend,
        "agent": build_agent_backend,
        "rag": build_rag_backend,
        "tts": build_tts_backend,
        "stt": build_stt_backend,
        "video": build_video_backend,
        "storage": build_storage_backend,
        "report": build_report_backend,
    }
    fn = builders.get(backend_type)
    if fn is None:
        logger.warning(f"build: unknown backend type {backend_type!r}")
        return None
    try:
        if backend_type == "video":
            return fn(cfg.get("base_dir"))
        return fn()
    except Exception as e:
        logger.warning(f"build({backend_type!r}) failed: {e}")
        return None
