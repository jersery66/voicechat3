"""Adapter conformance tests.

Two directions:
1. The integration fakes must satisfy every Protocol via isinstance
   (runtime_checkable) — guarantees the fake layer cannot drift from the
   contract.
2. The REAL production classes must expose every protocol method
   (class-level getattr check; all current services define methods at
   class level). Signatures are a review-checked invariant — see the
   Phase-2 stage-3 review notes.
3. Factory smoke: builders that need no GPU/audio are actually CALLED so
   constructor mistakes (missing required args) cannot hide.
"""

from adapters.protocols import (
    AgentBackend,
    LLMBackend,
    RAGBackend,
    ReportBackend,
    STTBackend,
    StorageBackend,
    TTSBackend,
    VideoBackend,
)
from tests.integration.fakes import (
    FakeAgent,
    FakeData,
    FakeLLM,
    FakeRAG,
    FakeReport,
    FakeSTT,
    FakeTTS,
    FakeVideo,
)


class TestFakesConform:
    def test_fake_llm(self):
        assert isinstance(FakeLLM(), LLMBackend)

    def test_fake_agent(self):
        assert isinstance(FakeAgent(), AgentBackend)

    def test_fake_rag(self):
        assert isinstance(FakeRAG(), RAGBackend)

    def test_fake_tts(self):
        assert isinstance(FakeTTS(), TTSBackend)

    def test_fake_stt(self):
        assert isinstance(FakeSTT(), STTBackend)

    def test_fake_video(self):
        assert isinstance(FakeVideo(), VideoBackend)

    def test_fake_data(self):
        assert isinstance(FakeData(), StorageBackend)

    def test_fake_report(self):
        assert isinstance(FakeReport(), ReportBackend)


class TestRealServicesConform:
    """Class-level surface checks on production classes. No heavy loading:
    model init happens in load_model()/first use, not here."""

    def test_llm_service(self):
        from services.llm_service import LLMService
        for name in ("chat", "reset_conversation", "set_history_context"):
            assert callable(getattr(LLMService, name, None)), name
        # conversation_history is an instance attribute set in __init__;
        # verify __init__ actually assigns it.
        import inspect
        src = inspect.getsource(LLMService.__init__)
        assert "conversation_history" in src

    def test_agent_service(self):
        from services.agent_service import AgentService
        for name in (
            "is_available",
            "route_conversation_actions",
            "classify_intent",
            "detect_emotion",
        ):
            assert callable(getattr(AgentService, name, None)), name
        assert not hasattr(AgentService, "assess_crisis_risk")
        assert not hasattr(AgentService, "_keyword_crisis_risk")

    def test_rag_service(self):
        from services.rag_service import RAGService
        assert callable(getattr(RAGService, "get_system_suffix", None))

    def test_tts_service(self):
        from services.tts_service_voxcpm import TTSService
        for name in ("generate_and_play", "stop_playing"):
            assert callable(getattr(TTSService, name, None)), name

    def test_stt_service(self):
        from services.stt_service import STTService
        for name in ("transcribe", "start_recording", "stop_recording",
                     "is_vad_triggered"):
            assert callable(getattr(STTService, name, None)), name

    def test_video_tool(self):
        from services.tools.video_tool import VideoPlayTool
        assert callable(getattr(VideoPlayTool, "execute", None))
        assert isinstance(getattr(VideoPlayTool, "FILE_MAP", None), dict)

    def test_data_manager(self):
        from data.data_manager import DataManager
        for name in ("set_user_id", "save_user_message",
                     "save_assistant_message", "start_new_session"):
            assert callable(getattr(DataManager, name, None)), name

    def test_report_service(self):
        from services.report_service import ReportService
        for name in ("start_session", "increment_round", "get_round_count",
                     "should_warn_time_limit", "is_over_limit"):
            assert callable(getattr(ReportService, name, None)), name


class TestFactorySmoke:
    """Actually call builders that need no GPU/audio/network. This catches
    constructor mistakes (e.g. missing required args) that pure attribute
    checks miss."""

    def test_build_video_backend_default_base_dir(self):
        from adapters.factory import build_video_backend
        tool = build_video_backend()
        assert callable(getattr(tool, "execute", None))
        assert isinstance(getattr(tool, "FILE_MAP", None), dict)

    def test_build_video_backend_custom_base_dir(self):
        from adapters.factory import build_video_backend
        tool = build_video_backend(base_dir=".")
        assert tool.base_dir == "."

    def test_build_storage_backend_returns_singleton(self):
        from adapters.factory import build_storage_backend
        from data.data_manager import get_data_manager
        assert build_storage_backend() is get_data_manager()

    def test_build_report_backend_returns_singleton(self):
        from adapters.factory import build_report_backend
        from services.report_service import get_report_service
        assert build_report_backend() is get_report_service()
