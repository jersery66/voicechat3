# Main Window - Background, left-right layout, loading transition, queue processing

import os
import sys
import queue
import time
import threading
import traceback
import re
import random

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QApplication, QLabel
)
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QFont, QPixmap, QPainter, QColor, QBrush

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.logger import get_logger

logger = get_logger(__name__)

from config import (
    APP_NAME, CRISIS_HOTLINES, GREETING_MESSAGE, GREETING_VARIANTS,
    POST_RELAXATION_MESSAGE, FILL_INFO_PROMPT, TRANSITION_PROMPT,
    SUGGESTIONS_PROMPT, CONTINUE_CHAT_MESSAGE, MIN_ROUNDS_FOR_RELAXATION,
    POST_RELAXATION_TIMEOUT, TIMEOUT_END_MESSAGE
)

from .control_panel import ControlPanel
from .chat_panel import ChatPanel
from .loading_screen import LoadingScreen
from .dialogs import (
    SessionEndDialog, CrisisDialog, ContinueOrEndDialog,
    WarningDialog
)
from .styles import get_style
from services.pipeline import get_end_type_enum, ConversationPipeline, PipelineConfig
from services.session_orchestrator import SessionState, SessionOrchestrator
from services.session_end_controller import SessionEndController
from services.report_service import EndType


class MainWindow(QMainWindow):
    """Main application window with background image, left-right panels."""

    def __init__(self):
        super().__init__()

        # Services (initialized later)
        self.stt_service = None
        self.llm_service = None
        self.tts_service = None
        self.data_manager = None
        self.report_service = None
        self.rag_service = None
        self.agent_service = None
        self.session_emotions = []  # Accumulated emotion tags for report
        self._scale_tags = {}
        self._session_ending = False
        self._pending_quit = False
        self._asking_scales = False
        self._user_explicit_end = False
        self._exit_wait_dialog = None

        # Tools (initialized in load_models; guarded against partial init)
        self.video_tool = None
        self.relaxation_tool = None
        self.report_tool = None

        # State
        self.is_recording = False
        self.models_loaded = False
        self._dark_mode = False
        self.orchestrator = SessionOrchestrator()
        self.session_end_controller = SessionEndController()
        self.user_info = {}
        self.current_user_id = None
        self.info_confirmed = False
        self.reset_timer_id = None

        # Queue for thread-safe UI updates
        self.processing_queue = queue.Queue()

        # Background pixmap
        self._bg_pixmap = None

        self._setup_window()
        self._setup_ui()
        self._connect_signals()

        # Start queue processor
        self._queue_timer = QTimer(self)
        self._queue_timer.timeout.connect(self.process_queue)
        self._queue_timer.start(50)

        # Progress bars update at a lower frequency (1s) — no need per-message
        self._progress_timer = QTimer(self)
        self._progress_timer.timeout.connect(self._update_progress_bars)
        self._progress_timer.start(1000)

        # Load models in background
        self.load_thread = threading.Thread(target=self.load_models, daemon=True)
        self.load_thread.start()

    def _setup_window(self):
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1024, 768)

        # Load background image
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bg_path = os.path.join(app_dir, "ui", "background.jpg")
        if os.path.exists(bg_path):
            self._bg_pixmap = QPixmap(bg_path)

        # Apply global stylesheet
        self.setStyleSheet(get_style())

    def _setup_ui(self):
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)

        # Main horizontal layout with bottom margin for logo
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 20, 0, 50)
        main_layout.setSpacing(0)

        # Left spacer
        main_layout.addSpacing(100)

        # Left control panel
        self.control_panel = ControlPanel()
        main_layout.addWidget(self.control_panel)

        # Center spacer (smaller - push panels closer to center)
        main_layout.addStretch(1)

        # Right chat panel
        self.chat_panel = ChatPanel()
        main_layout.addWidget(self.chat_panel)

        # Right spacer (smaller to let panel sit closer to edge)
        main_layout.addSpacing(100)

        # Loading screen (overlay on top)
        self.loading_screen = LoadingScreen(central)
        self.loading_screen.setGeometry(central.rect())
        self.loading_screen.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.loading_screen and self.loading_screen.isVisible():
            self.loading_screen.setGeometry(self.centralWidget().rect())
        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_T and event.modifiers() & Qt.ControlModifier:
            self._toggle_theme()
            return
        super().keyPressEvent(event)

    def _toggle_theme(self):
        from .widgets import MessageBubble
        self._dark_mode = not self._dark_mode
        self.setStyleSheet(get_style(dark=self._dark_mode))
        MessageBubble.set_dark_mode(self._dark_mode)
        for panel in [self.control_panel, self.chat_panel]:
            if hasattr(panel, 'set_theme'):
                panel.set_theme(self._dark_mode)

    def paintEvent(self, event):
        """Paint the background image."""
        if self._bg_pixmap:
            painter = QPainter(self)
            # Scale to fit within window, keep aspect ratio, no cropping
            scaled = self._bg_pixmap.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            # Center the image
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            # Fill edges with dominant background color
            painter.fillRect(0, 0, self.width(), self.height(), QColor(245, 245, 245))
            painter.drawPixmap(x, y, scaled)
            painter.end()
        super().paintEvent(event)

    def _connect_signals(self):
        # Control panel signals
        self.control_panel.confirm_user.connect(self._on_confirm_user)
        self.control_panel.modify_user.connect(self._on_modify_user)
        self.control_panel.record_started.connect(self._on_record_started)
        self.control_panel.record_stopped.connect(self._on_record_stopped)
        self.control_panel.play_breathing.connect(lambda: self._play_relaxation_video("breathing"))
        self.control_panel.play_muscle.connect(lambda: self._play_relaxation_video("muscle"))
        self.control_panel.play_meditation.connect(lambda: self._play_relaxation_video("meditation"))
        self.control_panel.play_game.connect(self._play_game)
        self.control_panel.play_media.connect(self._open_media_panel)

        # Chat panel signals
        self.chat_panel.clear_clicked.connect(self._clear_history)
        self.chat_panel.exit_clicked.connect(self._exit_app)
        self.chat_panel.text_submitted.connect(self._on_text_submitted)

    # ==================== User Info ====================

    def _on_confirm_user(self, info):
        normalized = self._normalize_user_info(info)
        user_id = normalized.get("user_id", "default_user")
        user_changed = self.current_user_id is not None and user_id != self.current_user_id

        self.user_info = normalized
        self.info_confirmed = True
        self.current_user_id = user_id

        if self.data_manager:
            self.data_manager.set_user_id(user_id)
            self.data_manager.save_user_profile(normalized)
            if self.llm_service:
                context = self.data_manager.get_formatted_history_context(
                    subject_id=user_id,
                    include_profile=True,
                    include_summaries=3,
                )
                self.llm_service.set_history_context(context)

        if user_changed:
            self._start_new_session()
        elif not self.data_manager.current_folder_name:
            self._start_new_session()

        if not user_changed and self.current_user_id == user_id:
            pass

        self._play_opening_greeting()
        self.control_panel.set_status("请等待问候后再录音")

    @staticmethod
    def _normalize_user_info(info):
        """Normalize UI field aliases used by storage, reports, and prompts."""
        normalized = dict(info or {})
        user_id = (
            normalized.get("user_id")
            or normalized.get("subject_id")
            or "default_user"
        )
        user_id = str(user_id).strip() or "default_user"
        normalized["user_id"] = user_id
        normalized["subject_id"] = user_id

        marital = normalized.get("marital_status") or normalized.get("marital")
        if marital:
            normalized["marital_status"] = marital
            normalized["marital"] = marital

        drug_type = normalized.get("addiction_type") or normalized.get("drug_type")
        if drug_type:
            normalized["addiction_type"] = drug_type
            normalized["drug_type"] = drug_type

        return normalized

    def _on_modify_user(self):
        self.info_confirmed = False
        self.control_panel.set_status("请重新填写信息")

    # ==================== Recording ====================

    def _on_record_started(self):
        if not self.models_loaded:
            self.control_panel.reset_recording()
            return

        # Stop any playing TTS
        if self.tts_service:
            self.tts_service.stop_playing()

        if not self.info_confirmed:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "请先填写信息", "请先填写并确认基本信息后再开始对话")
            self.control_panel.reset_recording()
            return

        # Check user ID change
        new_user_id = self.control_panel.get_user_info().get("user_id", "default_user")
        if self.current_user_id is not None and new_user_id != self.current_user_id:
            self._start_new_session()
            self._play_opening_greeting()
            self.control_panel.set_status("用户已更换，请等待问候后再录音")
            self.control_panel.reset_recording()
            return

        if self.current_user_id is None:
            self.current_user_id = new_user_id

        self.is_recording = True
        self.control_panel.set_recording_state(True)
        self.control_panel.set_status("正在录音...")
        self.control_panel.stop_all_blinks()

        if self.stt_service:
            threading.Thread(target=self.stt_service.start_recording, daemon=True).start()

    def _on_record_stopped(self):
        if not self.is_recording:
            return

        self.is_recording = False
        self.control_panel.set_recording_state(False)
        self.control_panel.set_status("正在处理...")

        if self.stt_service:
            threading.Thread(target=self._run_pipeline, daemon=True).start()
        else:
            self.control_panel.set_status("测试模式 - 服务未加载")
            self.control_panel.reset_recording()

    def _on_text_submitted(self, text):
        """Handle text input from chat panel."""
        if not text.strip():
            return
        if not self.models_loaded or not self.pipeline:
            self.chat_panel.add_system_message("模型尚未加载完成，请稍候")
            return
        if self.orchestrator.state in (SessionState.SESSION_ENDING, SessionState.SESSION_ENDED):
            self.chat_panel.add_system_message("会话已结束，请开始新对话")
            return
        if not self.info_confirmed:
            self.chat_panel.add_system_message("请先填写左侧基本信息并确认")
            return
        self.processing_queue.put(("status", "正在思考..."))
        threading.Thread(target=self._run_pipeline, args=(text,), daemon=True).start()

    # ==================== Pipeline ====================

    def _run_pipeline(self, text=None):
        """Unified pipeline entry point. Runs on a background thread.
        text=None for voice mode (STT+TTS), text=str for text mode."""
        try:
            if self.orchestrator.state in (SessionState.SESSION_ENDING, SessionState.SESSION_ENDED):
                self.processing_queue.put(("status", "会话已结束，请开始新对话"))
                return

            # Clear interim report
            self._interim_report = None
            self._interim_pdf_path = None

            if text is not None:
                self.processing_queue.put(("set_buttons_state", "disabled"))
                extra = getattr(self, '_pending_scale_prompt', '') or ''
                self._pending_scale_prompt = None
                config = PipelineConfig(use_stt=False, use_tts=True, user_text=text, extra_system_suffix=extra)
            else:
                # Voice mode: STT + TTS
                audio_data = self.stt_service.stop_recording()
                if len(audio_data) == 0:
                    self.processing_queue.put(("status", "未检测到语音"))
                    return
                extra = getattr(self, '_pending_scale_prompt', '') or ''
                self._pending_scale_prompt = None
                config = PipelineConfig(use_stt=True, use_tts=True, audio_data=audio_data, extra_system_suffix=extra)

            result = self.pipeline.execute(config, lambda mt, ct: self.processing_queue.put((mt, ct)))
            self._scale_tags = result.scale_tags
            self._post_pipeline_routing(result)

        except Exception as e:
            logger.exception("Exception occurred")
            self.processing_queue.put(("error", f"处理出错: {str(e)}"))
        finally:
            if text is not None:
                self.processing_queue.put(("set_buttons_state", "normal"))

    def _post_pipeline_routing(self, result):
        """Route pipeline result to appropriate actions."""
        if result.end_type:
            et = get_end_type_enum(result.end_type)
            if et == EndType.SAFETY and result.crisis_risk < 7:
                self.processing_queue.put(("show_crisis", None))
            self._handle_session_end(et, result.relaxation_rec)
        elif result.relaxation_rec and not self._asking_scales:
            self.processing_queue.put(("highlight_relax", result.relaxation_rec))
            self.processing_queue.put(("status", "准备就绪"))
        elif result.intent == "entertainment":
            self.processing_queue.put(("highlight_relax", "game"))
            self.processing_queue.put(("status", "准备就绪"))
        else:
            self.processing_queue.put(("status", "准备就绪"))

    # ==================== Queue Processing ====================

    def process_queue(self):
        # VAD auto-stop polling
        if (self.is_recording and self.stt_service
                and self.stt_service.is_vad_triggered()):
            self.is_recording = False
            self._on_record_stopped()

        try:
            while True:
                task = self.processing_queue.get_nowait()
                msg_type, content = task

                if msg_type == "status":
                    self.control_panel.set_status(content)

                elif msg_type == "append_chat":
                    role, text = content
                    if role == "user":
                        self.chat_panel.add_user_message(text)
                    elif role == "ai":
                        self.chat_panel.start_ai_message()
                        self.chat_panel.stream_text(text)
                        self.chat_panel.finish_streaming()
                    elif role == "ai_start":
                        self.chat_panel.start_ai_message()

                elif msg_type == "start_ai_message":
                    self.chat_panel.start_ai_message()

                elif msg_type == "stream_text":
                    self.chat_panel.stream_text(content)

                elif msg_type == "clear_last_ai":
                    if self.chat_panel._messages and self.chat_panel._messages[-1].get("type") == "ai":
                        bubble = self.chat_panel._messages[-1].get("bubble")
                        if bubble:
                            bubble.deleteLater()
                        self.chat_panel._messages.pop()
                        self.chat_panel._current_streaming_bubble = None

                elif msg_type == "finish_streaming":
                    self.chat_panel.finish_streaming()

                elif msg_type == "clean_last_ai":
                    if content and self.chat_panel._messages:
                        last = self.chat_panel._messages[-1]
                        if last.get("type") == "ai" and last.get("bubble"):
                            last["bubble"].set_text(content)
                            last["text"] = content
                            self.chat_panel._scroll_to_bottom()

                elif msg_type == "session_end":
                    end_type, feedback, relaxation_rec, audio_data = content[:4]
                    play_audio = content[4] if len(content) > 4 else True
                    self._show_session_end_dialog(end_type, feedback, relaxation_rec, audio_data, play_audio)

                elif msg_type == "show_crisis":
                    self._show_crisis_dialog(content)

                elif msg_type == "highlight_relax":
                    # Map Chinese tags to English keys for control_panel
                    relax_map = {
                        "呼吸": "breathing", "肌肉": "muscle", "冥想": "meditation", "游戏": "game",
                        "breathing": "breathing", "muscle": "muscle", "meditation": "meditation", "game": "game",
                    }
                    relax_key = relax_map.get(content, content)
                    self.control_panel.highlight_relax_button(relax_key)

                elif msg_type == "session_warning":
                    if content == "TIME_LIMIT_ASK":
                        self._ask_continue_or_end()
                    else:
                        self.chat_panel.add_system_message(content)

                elif msg_type == "time_limit_ask":
                    self._ask_continue_or_end()

                elif msg_type == "enable_ui":
                    self._transition_to_main()

                elif msg_type == "play_greeting":
                    QTimer.singleShot(1000, self._play_opening_greeting)

                elif msg_type == "fill_info_prompt":
                    QTimer.singleShot(1000, self._play_fill_info_prompt)

                elif msg_type == "start_keepalive":
                    self._start_ollama_keepalive()

                elif msg_type == "video_finished":
                    QTimer.singleShot(500, lambda c=content: self._on_video_finished(c))

                elif msg_type == "game_finished":
                    QTimer.singleShot(500, self._on_game_finished)

                elif msg_type == "loading_progress":
                    if self.loading_screen:
                        self.loading_screen.set_progress(content)

                elif msg_type == "loading_step":
                    if self.loading_screen:
                        self.loading_screen.set_step(content)

                elif msg_type == "set_buttons_state":
                    if content == "normal":
                        self.control_panel.set_buttons_enabled(True)
                    else:
                        self.control_panel.set_buttons_enabled(False)

                elif msg_type == "quit":
                    self._close_exit_dialog_and_quit()
                    return  # stop processing further queue items

                elif msg_type == "error":
                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.critical(self, "错误", content)
                    self.control_panel.set_status("发生错误")
                    self.control_panel.set_buttons_enabled(True)

                self.processing_queue.task_done()
        except queue.Empty:
            pass

    # ==================== Model Loading ====================

    def load_models(self):
        """Load all models with progress updates."""
        try:
            from services.stt_service import STTService
            from services.llm_service import LLMService
            from services.tts_service import TTSService
            from services.rag_service import get_rag_service
            from data.data_manager import DataManager
            from services.report_service import ReportService

            # Step 1: STT + TTS
            self.processing_queue.put(("loading_step", "步骤 1/3: 语音模块加载中"))
            self.processing_queue.put(("status", "正在并行加载语音识别与合成模型..."))
            self.processing_queue.put(("loading_progress", 5))

            self.tts_service = None

            # STT/TTS: load with graceful fallback. Either service may be
            # unavailable while text chat and reports remain usable.
            stt_ok = False
            tts_ok = False
            load_errors = []

            def load_stt():
                nonlocal stt_ok
                try:
                    service = STTService()
                    service.load_model()
                    self.stt_service = service
                    stt_ok = True
                except Exception as exc:
                    self.stt_service = None
                    load_errors.append(f"STT: {exc}")
                    logger.warning(f"STT load failed: {exc}")

            def load_tts():
                nonlocal tts_ok
                try:
                    service = TTSService()
                    service.load_model(use_streaming=True)
                    self.tts_service = service
                    tts_ok = True
                except Exception as exc:
                    self.tts_service = None
                    load_errors.append(f"TTS: {exc}")
                    logger.warning(f"TTS load failed: {exc}")

            try:
                from config import FUNASR_MODEL_PATH, VOXCPM_MODEL_PATH

                threads = []
                if FUNASR_MODEL_PATH and os.path.isdir(FUNASR_MODEL_PATH):
                    t_stt = threading.Thread(target=load_stt, daemon=True)
                    t_stt.start()
                    threads.append(t_stt)
                else:
                    self.processing_queue.put(("status", "语音识别模型未找到，跳过 STT"))

                t_tts = threading.Thread(target=load_tts, daemon=True)
                t_tts.start()
                threads.append(t_tts)

                for thread in threads:
                    thread.join()
                if load_errors:
                    self.processing_queue.put(("status", "部分语音模块不可用，已降级继续"))
            except Exception as e:
                logger.warning(f"Voice service loading failed: {e}")
                self.processing_queue.put(("status", f"语音模块加载失败: {e}"))

            self.processing_queue.put(("loading_progress", 40))

            if stt_ok and self.stt_service:
                self.processing_queue.put(("status", "正在预热语音识别..."))
                self.stt_service.warmup()
            self.processing_queue.put(("loading_progress", 50))

            if tts_ok and self.tts_service:
                self.processing_queue.put(("status", "正在预热语音合成..."))
                self.tts_service.warmup()
            else:
                logger.warning("TTS unavailable; continuing in text-only playback mode")
            self.processing_queue.put(("loading_progress", 65))

            # Step 2: LLM
            self.processing_queue.put(("loading_step", "步骤 2/3: 智能对话模块"))
            self.processing_queue.put(("status", "正在连接智能助手..."))
            self.processing_queue.put(("loading_progress", 70))
            self.llm_service = LLMService()
            if not self.llm_service.test_connection():
                self.processing_queue.put(("error", "无法连接到 Ollama 服务"))
                return
            self.processing_queue.put(("loading_progress", 80))
            self.processing_queue.put(("status", "正在预热智能助手..."))
            self.llm_service.warmup()
            self.processing_queue.put(("loading_progress", 90))

            # Step 2.5: Agent (3B routing model)
            try:
                from services.agent_service import get_agent_service
                self.agent_service = get_agent_service()
                if self.agent_service.is_available():
                    logger.info("Agent service (3B) ready")
                else:
                    logger.warning("3B agent model not available, using keyword fallback")
            except Exception as e:
                logger.warning(f"Agent service init failed: {e}")
                self.agent_service = None

            # Step 3: Data + Report + RAG
            self.processing_queue.put(("loading_step", "步骤 3/3: 初始化数据"))
            self.processing_queue.put(("status", "正在初始化数据管理..."))
            self.data_manager = DataManager()

            self.report_service = ReportService(self.llm_service, agent_service=self.agent_service)
            self.report_service.start_session()
            self.orchestrator.transition_to(SessionState.CHATTING)

            try:
                self.rag_service = get_rag_service()
            except Exception as e:
                logger.warning(f"RAG service init failed: {e}")
                self.rag_service = None

            # Unified conversation pipeline
            from services.emotion_tracker import EmotionTracker
            self.emotion_tracker = EmotionTracker()
            self.pipeline = ConversationPipeline(
                stt_service=self.stt_service,
                llm_service=self.llm_service,
                tts_service=self.tts_service,
                rag_service=self.rag_service,
                agent_service=self.agent_service,
                report_service=self.report_service,
                data_manager=self.data_manager,
                session_emotions=self.session_emotions,
                emotion_tracker=self.emotion_tracker,
            )

            # Tools
            from services.tools.video_tool import VideoPlayTool
            from services.tools.relaxation_tool import RelaxationRecommendationTool
            from services.tools.report_tool import ReportGenerationTool
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.video_tool = VideoPlayTool(app_dir)
            self.relaxation_tool = RelaxationRecommendationTool(
                self.agent_service, self.report_service
            )
            self.report_tool = ReportGenerationTool(
                self.report_service, self.data_manager
            )

            self.processing_queue.put(("loading_progress", 100))
            self.processing_queue.put(("loading_step", "加载完成"))
            self.processing_queue.put(("status", "准备就绪"))

            time.sleep(0.3)
            self.models_loaded = True
            self.processing_queue.put(("enable_ui", None))
            self.processing_queue.put(("fill_info_prompt", None))
            self.processing_queue.put(("start_keepalive", None))

        except Exception as e:
            logger.exception("Exception occurred")
            self._cleanup_partial_services()
            self.processing_queue.put(("error", f"模型加载失败: {str(e)}"))

    # ==================== UI Transitions ====================

    def _transition_to_main(self):
        """Transition from loading screen to main UI."""
        self.loading_screen.set_loading_complete()
        self.loading_screen.fade_out(callback=self.loading_screen.hide)
        self.control_panel.set_buttons_enabled(True)

    # ==================== Session Management ====================

    def _start_new_session(self):
        self.chat_panel.clear_chat()
        self.session_emotions = []
        self._scale_tags = {}
        self._session_ending = False
        self._asking_scales = False
        self._user_explicit_end = False
        self._pending_scale_prompt = None
        self.session_end_controller.reset()
        self.orchestrator.reset()
        if hasattr(self, 'emotion_tracker') and self.emotion_tracker:
            self.emotion_tracker.reset()
        if hasattr(self, 'pipeline') and self.pipeline:
            self.pipeline.reset_session()
        if self.llm_service:
            self.llm_service.reset_conversation()
            if self.data_manager and self.current_user_id:
                context = self.data_manager.get_formatted_history_context(
                    subject_id=self.current_user_id,
                    include_profile=True,
                    include_summaries=3
                )
                if context:
                    self.llm_service.set_history_context(context)
        if self.data_manager:
            self.data_manager.start_new_session()
        if self.report_service:
            self.report_service.start_session()

    def _show_session_end_dialog(self, end_type, feedback, relaxation_rec, audio_data, play_audio=True):
        self.orchestrator.transition_to(SessionState.SESSION_ENDED)
        report_path = getattr(self, '_interim_pdf_path', None)
        dialog = SessionEndDialog(
            self, end_type, feedback, relaxation_rec,
            report_path=report_path, play_audio=play_audio
        )
        if dialog.exec():
            self._start_new_session()
            self._play_opening_greeting()

    def _show_crisis_dialog(self, risk_data=None):
        risk_level = risk_data.get("risk_level", 0) if risk_data else 0
        indicators = risk_data.get("indicators", []) if risk_data else []
        dialog = CrisisDialog(self, CRISIS_HOTLINES, risk_level, indicators)
        dialog.exec()

    def _start_post_relaxation_timeout(self):
        """启动放松后超时定时器（60秒无操作自动结束）"""
        self._post_relaxation_timer = QTimer(self)
        self._post_relaxation_timer.setSingleShot(True)
        self._post_relaxation_timer.timeout.connect(self._on_post_relaxation_timeout_trigger)
        self._post_relaxation_timer.start(POST_RELAXATION_TIMEOUT * 1000)

    def _on_post_relaxation_timeout_trigger(self):
        """超时定时器触发：关闭弹窗，标记超时"""
        self.orchestrator.ctx.post_relaxation_timed_out = True
        if hasattr(self, '_post_relaxation_dialog') and self._post_relaxation_dialog:
            self._post_relaxation_dialog.close()

    def _on_post_relaxation_timeout(self):
        """放松后超时：播放结束语 → 生成报告结束会话"""
        if self.orchestrator.state != SessionState.POST_RELAXATION:
            return
        message = random.choice(TIMEOUT_END_MESSAGE)
        self.chat_panel.add_system_message(message)
        self._play_tts_async(message)
        QTimer.singleShot(5000, lambda: self._handle_session_end(EndType.TIME_LIMIT))

    def _cancel_post_relaxation_timer(self):
        """取消放松后超时定时器（用户做出选择或关闭弹窗时调用）"""
        if hasattr(self, '_post_relaxation_timer') and self._post_relaxation_timer.isActive():
            self._post_relaxation_timer.stop()

    def _on_continue_chosen(self):
        """用户选择继续聊天"""
        self._cancel_post_relaxation_timer()
        self.orchestrator.transition_to(SessionState.CHATTING)
        message = random.choice(CONTINUE_CHAT_MESSAGE)
        self.chat_panel.add_system_message(message)
        self._play_tts_async(message)

    def _on_end_chosen(self):
        """用户在放松后弹窗选择结束"""
        self._cancel_post_relaxation_timer()
        self._handle_session_end(EndType.GOAL_ACHIEVED)

    # ==================== Video / Game ====================

    def _play_relaxation_video(self, relaxation_type):
        """统一的放松视频播放流程：全屏播放 → 记录 → 弹窗
        relaxation_type: 'breathing', 'muscle', 'meditation'
        """
        if not self.orchestrator.can_play_video():
            return

        self.orchestrator.transition_to(SessionState.VIDEO_PLAYING)
        self.control_panel.stop_all_blinks()

        # Record relaxation BEFORE video (authoritative record)
        relax_name = self.video_tool.FILE_MAP.get(relaxation_type, "").replace(".mp4", "")
        if relax_name:
            self.orchestrator.ctx.current_relaxation_type = relax_name
            if self.report_service:
                self.report_service.record_relaxation(relax_name)
                self.report_service.activity_log.append({
                    "type": "relaxation",
                    "relaxation_type": relaxation_type,
                    "relaxation_name": relax_name,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })

        def video_runner():
            try:
                self.video_tool.execute(relaxation_type=relaxation_type)
            except Exception as e:
                logger.warning(f"Video error: {e}")
            finally:
                self.processing_queue.put(("video_finished", relaxation_type))

        threading.Thread(target=video_runner, daemon=True).start()

    def _on_video_finished(self, relaxation_type):
        """视频播放完成后的处理：问候 → 弹窗 + 超时"""
        self.orchestrator.transition_to(SessionState.POST_RELAXATION)

        # NOTE: record_relaxation already called in _play_relaxation_video before video plays

        # 1. 播放放松后问候
        self._play_post_relaxation_greeting()

        # 2. 启动超时定时器
        self.orchestrator.ctx.post_relaxation_timed_out = False
        self._start_post_relaxation_timeout()

        # 3. 显示继续/结束弹窗
        dialog = ContinueOrEndDialog(self)
        self._post_relaxation_dialog = dialog
        dialog.continue_chosen.connect(self._on_continue_chosen)
        dialog.end_chosen.connect(self._on_end_chosen)

        # X-button close = treat as "continue chatting" (unless timeout fired)
        def on_dialog_finished():
            self._cancel_post_relaxation_timer()
            if self.orchestrator.state == SessionState.POST_RELAXATION:
                if self.orchestrator.ctx.post_relaxation_timed_out:
                    self._on_post_relaxation_timeout()
                else:
                    self._on_continue_chosen()

        dialog.finished.connect(on_dialog_finished)
        dialog.exec()
        self._post_relaxation_dialog = None

    def _play_game(self):
        if not self.orchestrator.can_play_video():
            return
        self.orchestrator.transition_to(SessionState.VIDEO_PLAYING)
        self.control_panel.stop_all_blinks()

        def game_runner():
            try:
                from services.game_service import get_game_service
                game = get_game_service()
                game.launch()
            except Exception as e:
                logger.warning(f"Game error: {e}")
            finally:
                self.processing_queue.put(("game_finished", None))

        threading.Thread(target=game_runner, daemon=True).start()

    def _on_game_finished(self):
        """Game finished — greeting + continue/end dialog, same as post-relaxation."""
        self.orchestrator.transition_to(SessionState.POST_RELAXATION)

        # Post-game greeting
        fallback = random.choice(POST_RELAXATION_MESSAGE) if POST_RELAXATION_MESSAGE else "玩完啦，感觉怎么样？放松一点了吗？"
        self.chat_panel.add_system_message(fallback)
        self._play_tts_async(fallback)

        # Timeout timer
        self.orchestrator.ctx.post_relaxation_timed_out = False
        self._start_post_relaxation_timeout()

        # Continue/end dialog
        dialog = ContinueOrEndDialog(self)
        self._post_relaxation_dialog = dialog
        dialog.continue_chosen.connect(self._on_continue_chosen)
        dialog.end_chosen.connect(self._on_end_chosen)

        def on_dialog_finished():
            self._cancel_post_relaxation_timer()
            if self.orchestrator.state == SessionState.POST_RELAXATION:
                if self.orchestrator.ctx.post_relaxation_timed_out:
                    self._on_post_relaxation_timeout()
                else:
                    self._on_continue_chosen()

        dialog.finished.connect(on_dialog_finished)
        dialog.exec()
        self._post_relaxation_dialog = None

    def _ask_continue_or_end(self):
        """Ask user whether to continue or end when time limit is reached."""
        from ui.dialogs import ContinueOrEndDialog
        dialog = ContinueOrEndDialog(parent=self)
        dialog.setWindowTitle("会话时间提醒")
        dialog._setup_ui_for_timeout()
        dialog.continue_chosen.connect(self._on_timeout_continue)
        def _on_end():
            self._user_explicit_end = True
            self._handle_session_end(EndType.TIME_LIMIT)
        dialog.end_chosen.connect(_on_end)
        dialog.exec()

    def _on_timeout_continue(self):
        """User chose to continue after time limit."""
        self.report_service.time_warning_shown = False
        self.chat_panel.add_system_message("好的，咱们继续聊。")
        self._play_tts_async("好的，咱们继续聊。")
        self.control_panel.set_status("继续对话中...")

    def _open_media_panel(self):
        try:
            import json
            from config import MEDIA_LIBRARY_PATH
            from ui.media_panel import MediaPanelDialog
            config_path = os.path.join(MEDIA_LIBRARY_PATH, "library_config.json")
            library_config = {}
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    library_config = json.load(f)
            dialog = MediaPanelDialog(library_config=library_config, parent=self)
            dialog.scene_selected.connect(self._on_media_scene_selected)
            dialog.open_url.connect(self._on_media_url_opened)
            dialog.exec()
        except Exception as e:
            logger.warning(f"Media panel error: {e}")

    def _on_media_scene_selected(self, scene_key):
        try:
            from config import MEDIA_LIBRARY_PATH
            import json
            config_path = os.path.join(MEDIA_LIBRARY_PATH, "library_config.json")
            with open(config_path, 'r', encoding='utf-8') as f:
                library_config = json.load(f)
            scene = library_config.get("scenes", {}).get(scene_key, {})
            scene_name = scene.get("name", scene_key)
            music_files = scene.get("music", [])
            video_files = scene.get("videos", [])

            if self.report_service and not self._session_ending:
                self.report_service.activity_log.append({
                    "type": "media",
                    "scene": scene_key,
                    "scene_name": scene_name,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })

            if video_files:
                from services.video_service import VideoPlayer
                player = VideoPlayer()
                video_path = os.path.join(MEDIA_LIBRARY_PATH, video_files[0])
                player.play_video(video_path)
            elif music_files:
                import webbrowser
                music_path = os.path.join(MEDIA_LIBRARY_PATH, music_files[0])
                webbrowser.open(music_path)
            else:
                self.chat_panel.add_system_message(f"暂无本地资源，可点击免费下载获取{scene_name}相关内容")
        except Exception as e:
            logger.warning(f"Media scene error: {e}")

    def _on_media_url_opened(self, url):
        import webbrowser
        webbrowser.open(url)

    # ==================== Audio Playback ====================

    def _play_tts_async(self, text):
        """Play TTS in the background when the service is available."""
        if not self.tts_service or not text:
            return

        def runner():
            try:
                self.tts_service.generate_and_play(text)
            except Exception as e:
                logger.warning(f"TTS playback failed: {e}")

        threading.Thread(target=runner, daemon=True).start()

    def _play_opening_greeting(self):
        """Play the opening greeting - dynamically generated or fallback."""
        fallback = random.choice(GREETING_VARIANTS) if GREETING_VARIANTS else GREETING_MESSAGE
        self.chat_panel.add_system_message(fallback, as_ai=True)
        self._play_tts_async(fallback)

        def _try_generate():
            generated = ""
            try:
                if self.agent_service and self.agent_service.is_available():
                    generated = self.agent_service.generate_greeting(timeout=5.0)
                elif self.llm_service:
                    generated = self.llm_service.generate_short_text(
                        "你是薇薇老师，一位温暖亲切的心理咨询师。请生成一句简短欢迎问候语（不超过30字），口语化有温度像老朋友打招呼。只输出问候语本身。",
                        max_tokens=60
                    )
            except Exception as e:
                logger.debug(f"Greeting generation failed: {e}")
            if generated and len(generated.strip()) > 5:
                generated = generated.strip()
                if len(generated) > 60:
                    generated = generated[:60]
                QTimer.singleShot(0, lambda g=generated: self._replace_greeting(g))
        threading.Thread(target=_try_generate, daemon=True).start()

    def _replace_greeting(self, new_greeting):
        """Replace the last AI message with a new greeting."""
        msgs = self.chat_panel._messages
        if msgs and msgs[-1]["type"] == "ai":
            bubble = msgs[-1]["bubble"]
            bubble._full_text = new_greeting
            bubble.text_label.setText(new_greeting)
            self._play_tts_async(new_greeting)

    def _play_post_relaxation_greeting(self):
        """Play post-relaxation greeting - dynamically generated or fallback."""
        fallback = random.choice(POST_RELAXATION_MESSAGE) if POST_RELAXATION_MESSAGE else "做完啦，身上有没有舒服点呀？"
        self.chat_panel.add_system_message(fallback)
        self._play_tts_async(fallback)

        relax_type = self.orchestrator.ctx.current_relaxation_type or ""
        relax_name = {"breathing": "呼吸放松", "muscle": "肌肉放松", "meditation": "冥想"}.get(relax_type, "放松训练")

        def _try_generate():
            generated = ""
            try:
                if self.agent_service and self.agent_service.is_available():
                    generated = self.agent_service.generate_post_relaxation_greeting(relax_type, timeout=5.0)
                elif self.llm_service:
                    generated = self.llm_service.generate_short_text(
                        f"你是薇薇老师。来访者刚完成{relax_name}训练，生成一句简短关心问候（不超过25字）。只输出问候语本身。",
                        max_tokens=50
                    )
            except Exception as e:
                logger.debug(f"Post-relaxation greeting failed: {e}")
            if generated and len(generated.strip()) > 3:
                generated = generated.strip()
                if len(generated) > 50:
                    generated = generated[:50]
                QTimer.singleShot(0, lambda g=generated: self._replace_last_system(g))
        threading.Thread(target=_try_generate, daemon=True).start()

    def _replace_last_system(self, new_text):
        """Replace the last system message text."""
        msgs = self.chat_panel._messages
        if msgs and msgs[-1]["type"] == "system":
            bubble = msgs[-1]["bubble"]
            bubble._full_text = new_text
            bubble.text_label.setText(new_text)
            self._play_tts_async(new_text)

    def _play_fill_info_prompt(self):
        """Play fill-info prompt - dynamically generated or fallback."""
        fallback = FILL_INFO_PROMPT
        self._play_tts_async(fallback)

        def _try_generate():
            generated = ""
            try:
                if self.agent_service and self.agent_service.is_available():
                    generated = self.agent_service.generate_fill_info_prompt(timeout=5.0)
                elif self.llm_service:
                    generated = self.llm_service.generate_short_text(
                        "你是薇薇老师。生成一句简短的话引导来访者填写左边的基本信息并点确认（不超过30字）。只输出这句话本身。",
                        max_tokens=50
                    )
            except Exception as e:
                logger.debug(f"Fill-info prompt generation failed: {e}")
            if generated and len(generated.strip()) > 5:
                self._play_tts_async(generated.strip()[:50])
        threading.Thread(target=_try_generate, daemon=True).start()

    def _start_ollama_keepalive(self):
        """Keep Ollama model warm."""
        self._keepalive_timer = QTimer(self)
        self._keepalive_timer.timeout.connect(self._ollama_keepalive_tick)
        self._keepalive_timer.start(180000)  # 3 minutes

    def _ollama_keepalive_tick(self):
        if self.llm_service:
            threading.Thread(
                target=self.llm_service.warmup, daemon=True
            ).start()

    # ==================== Actions ====================

    def _clear_history(self):
        self._start_new_session()
        self._play_opening_greeting()

    def _exit_app(self):
        if self._session_ending:
            self._show_exit_waiting_dialog()
            return

        if self.models_loaded and self.orchestrator.state not in (SessionState.SESSION_ENDED, SessionState.IDLE):
            # Determine what's incomplete
            incomplete = []
            if hasattr(self, 'pipeline') and self.pipeline:
                incomplete = self.pipeline.get_incomplete_scales()
            has_relaxed = bool(self.orchestrator.ctx.current_relaxation_type)

            if incomplete or not has_relaxed:
                from PySide6.QtWidgets import QMessageBox, QPushButton
                box = QMessageBox(self)
                box.setWindowTitle("")
                buttons = {}
                if incomplete and not has_relaxed:
                    box.setText("在结束之前，你想再聊几个问题，还是试试放松训练？")
                    buttons["questions"] = box.addButton("再问几个问题", QMessageBox.AcceptRole)
                    buttons["relax"] = box.addButton("试试放松训练", QMessageBox.AcceptRole)
                    buttons["exit"] = box.addButton("直接退出", QMessageBox.RejectRole)
                elif incomplete:
                    box.setText("我还有几个问题想问你，你想继续聊聊天吗？")
                    buttons["questions"] = box.addButton("继续聊天", QMessageBox.AcceptRole)
                    buttons["exit"] = box.addButton("直接退出", QMessageBox.RejectRole)
                else:
                    box.setText("要不要试试放松训练？只需几分钟，效果很好的。")
                    buttons["relax"] = box.addButton("试试放松训练", QMessageBox.AcceptRole)
                    buttons["exit"] = box.addButton("直接退出", QMessageBox.RejectRole)
                box.exec()
                clicked = box.clickedButton()
                if clicked == buttons.get("questions"):
                    self._ask_remaining_scales()
                    return
                if clicked == buttons.get("relax"):
                    self._exit_with_relaxation()
                    return
                # "直接退出" — fast quit, no farewell/TTS/report
                self._force_quit_now()
                return

            # No incomplete items — offer "结束会话" (full) or "直接退出" (fast)
            from PySide6.QtWidgets import QMessageBox, QPushButton
            box = QMessageBox(self)
            box.setWindowTitle("退出确认")
            box.setText("确定要退出吗？")
            btn_end = box.addButton("结束会话并退出", QMessageBox.AcceptRole)
            btn_quit = box.addButton("直接退出", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() == btn_end:
                self._pending_quit = True
                self._user_explicit_end = True
                self._show_exit_waiting_dialog()
                self._handle_session_end(EndType.QUIT)
            elif box.clickedButton() == btn_quit:
                self._force_quit_now()
        else:
            self._force_quit_now()

    def _exit_with_relaxation(self):
        """Start relaxation training, then exit after it completes."""
        self._pending_quit = True
        tag = "呼吸"
        if self.relaxation_tool:
            try:
                tag = self.relaxation_tool.execute(
                    conversation_history=self.llm_service.conversation_history
                )
            except Exception:
                pass
        tag_cn_map = {"breathing": "呼吸", "muscle": "肌肉放松", "meditation": "冥想", "呼吸": "呼吸", "肌肉": "肌肉放松", "冥想": "冥想"}
        tag_cn = tag_cn_map.get(tag, tag)
        rec_text = f"好的，我们先做个{tag_cn}放松训练，做完再走。"
        self.chat_panel.add_system_message(rec_text)
        self._play_tts_async(rec_text)
        self.orchestrator.ctx.current_relaxation_type = tag
        QTimer.singleShot(1500, lambda: self._play_relaxation_video(tag))

    def _ask_remaining_scales(self):
        """Ask remaining scale questions in conversation via LLM."""
        if not hasattr(self, 'pipeline') or not self.pipeline:
            return
        remaining_prompt = self.pipeline.get_remaining_scale_prompt()
        if not remaining_prompt:
            return
        # Store for next pipeline run
        self._pending_scale_prompt = remaining_prompt
        self._asking_scales = True
        self.chat_panel.add_system_message("我再问你几个小问题，很快就好。")
        # Send a trigger message with scale questions as system context
        threading.Thread(
            target=self._run_pipeline_with_scale_context,
            daemon=True
        ).start()

    def _run_pipeline_with_scale_context(self):
        """Run pipeline with remaining scale questions as system context."""
        try:
            extra_suffix = getattr(self, '_pending_scale_prompt', '')
            self._pending_scale_prompt = None
            config = PipelineConfig(
                use_stt=False, use_tts=True,
                user_text="好的",
                extra_system_suffix=extra_suffix,
            )
            result = self.pipeline.execute(
                config, lambda mt, ct: self.processing_queue.put((mt, ct))
            )
            self._scale_tags = result.scale_tags
            self._post_pipeline_routing(result)
            # Check if more scale questions remain; if not, clear flag
            if self.pipeline.get_incomplete_scales():
                # Still have unanswered questions — keep _asking_scales True
                # and prepare next round of questions
                remaining = self.pipeline.get_remaining_scale_prompt()
                if remaining:
                    self._pending_scale_prompt = remaining
                    self._asking_scales = True
                    return
            # All done or no more questions
            self._asking_scales = False
        except Exception as e:
            logger.exception("Exception occurred")
            self._asking_scales = False
            self.processing_queue.put(("error", f"处理出错: {str(e)}"))

    def _force_quit_now(self):
        """Immediate quit — stop all services and exit, no farewell/report."""
        self._session_ending = True
        # Stop timers
        for timer_name in ("_queue_timer", "_progress_timer", "_keepalive_timer"):
            timer = getattr(self, timer_name, None)
            if timer:
                try:
                    timer.stop()
                except Exception:
                    pass
        # Stop audio
        try:
            if self.stt_service:
                self.stt_service.stop_recording()
        except Exception:
            pass
        try:
            if self.tts_service:
                self.tts_service.stop_playing()
                self.tts_service.cleanup()
        except Exception:
            pass
        # Shutdown pipeline executor
        try:
            if hasattr(self, "pipeline") and self.pipeline:
                self.pipeline.shutdown()
        except Exception:
            pass
        QApplication.quit()

    def _show_exit_waiting_dialog(self):
        """Show a non-modal 'processing' dialog until quit happens.

        Must be non-modal — a modal dialog would block the main event loop,
        preventing the background report thread from draining processing_queue,
        which causes a deadlock.
        """
        if hasattr(self, '_exit_wait_dialog') and self._exit_wait_dialog is not None:
            return  # already shown
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtGui import QFont
        import os as _os
        dlg = QDialog(self)
        dlg.setWindowTitle("正在退出")
        dlg.setModal(False)  # non-modal: event loop keeps running
        dlg.setMinimumSize(320, 140)
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowCloseButtonHint)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(30, 25, 30, 25)
        label = QLabel("正在处理，请稍候...")
        label.setFont(QFont("Microsoft YaHei", 13))
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        self._exit_wait_dialog = dlg
        dlg.show()
        # Safety: force-quit after 30s if normal quit path fails
        QTimer.singleShot(30000, lambda: _os._exit(0))

    def _close_exit_dialog_and_quit(self):
        """Close the waiting dialog (if any) and quit the application."""
        if self._exit_wait_dialog:
            self._exit_wait_dialog.close()
            self._exit_wait_dialog = None
        QApplication.quit()

    def _cleanup_partial_services(self):
        """Safely release any services that were partially initialized."""
        for svc_name in ("tts_service", "stt_service", "llm_service"):
            svc = getattr(self, svc_name, None)
            if svc is None:
                continue
            try:
                cleanup = getattr(svc, "cleanup", None)
                if cleanup:
                    cleanup()
            except Exception as e:
                logger.debug(f"Error cleaning up {svc_name}: {e}")
        if hasattr(self, "pipeline") and self.pipeline:
            try:
                self.pipeline.shutdown()
            except Exception:
                pass

    def closeEvent(self, event):
        self._cleanup_partial_services()
        event.accept()

    # ==================== Missing Business Logic ====================

    def _update_progress_bars(self):
        """Refresh session progress bars on control panel."""
        from config import MAX_CONVERSATION_MINUTES, MAX_CONVERSATION_ROUNDS
        if self.report_service and self.orchestrator.state == SessionState.CHATTING:
            elapsed = self.report_service.get_session_duration_minutes()
            rounds = self.report_service.get_round_count()
            self.control_panel.update_session_progress(
                elapsed, MAX_CONVERSATION_MINUTES, rounds, MAX_CONVERSATION_ROUNDS
            )

    def _get_relaxation_info_str(self):
        """Get Chinese description of relaxation training."""
        relax_map = {
            "huxi": "呼吸放松训练", "jirou": "渐进式肌肉放松",
            "mingxiang": "冥想正念训练", "冥想训练": "冥想正念训练",
            "肌肉放松训练": "渐进式肌肉放松", "肌肉放松": "渐进式肌肉放松",
            "呼吸放松训练": "呼吸放松训练", "呼吸训练": "呼吸放松训练",
            "unknown": "未知"
        }
        raw_type = self.orchestrator.ctx.current_relaxation_type or ""
        if raw_type:
            clean_type = raw_type.replace(".mp4", "")
            return relax_map.get(clean_type, clean_type)
        return "未进行"

    def _generate_and_save_pdf(self, researcher_report, user_id, end_type, save_result=None):
        """Generate PDF report."""
        try:
            from services.report_generator import get_pdf_generator
            pdf_generator = get_pdf_generator()
            pdf_data = researcher_report.copy() if isinstance(researcher_report, dict) else {}
            if "subject_id" not in pdf_data:
                pdf_data["subject_id"] = user_id

            end_type_value = end_type.value if hasattr(end_type, 'value') else str(end_type)
            pdf_data.update({
                "report_date": self.report_service.get_session_start_time().strftime("%Y年%m月%d日") if self.report_service.session_start_time else "未知",
                "session_duration_minutes": self.report_service.get_session_duration_minutes(),
                "conversation_rounds": self.report_service.round_count,
                "end_type": end_type_value,
            })

            session_folder = None
            if save_result:
                session_folder = os.path.dirname(save_result.get("report_path", "")) if save_result else None
            if not session_folder or not os.path.isdir(session_folder):
                session_folder = getattr(self.data_manager, 'session_dir', None)

            if session_folder and os.path.isdir(session_folder):
                pdf_path = pdf_generator.generate_report(pdf_data, session_folder)
                if pdf_path:
                    logger.info(f"PDF报告已生成: {pdf_path}")
                    return pdf_path
        except Exception as e:
            logger.warning(f"PDF生成失败: {e}")
            logger.exception("Exception occurred")
        return None

    def _handle_session_end(self, end_type, relaxation_tag=None):
        """Handle session end using orchestrator decision logic."""
        if not self.session_end_controller.begin().accepted:
            return
        self._session_ending = True

        # If user explicitly chose to end (from time-limit dialog or exit button),
        # skip forced relaxation — respect their choice.
        user_explicit = self._user_explicit_end
        self._user_explicit_end = False  # consume the flag

        # Use orchestrator to decide: force relaxation or generate reports
        if not user_explicit and not relaxation_tag and not self.orchestrator.ctx.current_relaxation_type:
            if self.relaxation_tool is None:
                relaxation_tag = "呼吸"
            else:
                try:
                    conversation_history = self.llm_service.conversation_history
                    relaxation_tag = self.relaxation_tool.execute(
                        conversation_history=conversation_history
                    )
                except Exception as e:
                    logger.warning(f"智能推荐失败: {e}")
                    relaxation_tag = "呼吸"

        action, data = self.orchestrator.evaluate_session_end(end_type, relaxation_tag)

        if action == "force_relaxation":
            tag = data["relaxation_tag"]
            tag_cn_map = {"breathing": "呼吸", "muscle": "肌肉放松", "meditation": "冥想", "呼吸": "呼吸", "肌肉": "肌肉放松", "冥想": "冥想"}
            tag_cn = tag_cn_map.get(tag, tag)
            rec_text = f"等等，在结束之前，我留意到你还是有点紧张。要不咱们先做个{tag_cn}放松训练？只需几分钟，效果很好的。"
            self.processing_queue.put(("append_chat", ("ai", rec_text)))
            self._play_tts_async(rec_text)
            QTimer.singleShot(1000, lambda: self.processing_queue.put(("highlight_relax", tag)))
            self.processing_queue.put(("status", "请尝试放松训练"))
            self._session_ending = False
            self.session_end_controller.defer_for_relaxation()
            return

        # action == "generate_reports"
        self.processing_queue.put(("status", "正在生成告别语..."))

        def generate_farewell_and_reports():
            try:
                user_id = self.current_user_id or "default_user"
                current_user_info = getattr(self, "user_info", {})
                conversation_history = self.llm_service.conversation_history
                relax_str = self._get_relaxation_info_str()

                relaxation_rec = None
                if relaxation_tag:
                    tag_map = {"呼吸": "BREATHING", "肌肉": "MUSCLE", "冥想": "MEDITATION"}
                    relaxation_rec = tag_map.get(relaxation_tag)

                # --- Phase 1: Generate farewell text via LLM streaming ---
                self.processing_queue.put(("start_ai_message", None))
                full_feedback = ""
                try:
                    stream_gen = self.report_service.generate_visitor_feedback(
                        conversation_history, end_type, relaxation_rec or relax_str,
                        stream=True, session_emotions=self.session_emotions
                    )
                    for chunk in stream_gen:
                        full_feedback += chunk
                        clean = re.sub(r'<\|[^>]+\|>', '', chunk)
                        clean = re.sub(r'\[REC_[A-Z_]+\]', '', clean)
                        clean = re.sub(r'\[END_[A-Z_]+\]', '', clean)
                        clean = re.sub(r'\[SCALE:[^]]+\]', '', clean)
                        if clean.strip():
                            self.processing_queue.put(("stream_text", clean.strip()))
                except Exception as e:
                    logger.warning(f"Visitor feedback generation failed: {e}")
                    full_feedback = "今天的聊天到此结束，希望对你有所帮助。有事儿随时来找我唠。"

                self.processing_queue.put(("finish_streaming", None))

                if end_type == EndType.SAFETY:
                    self.processing_queue.put(("show_crisis", None))

                self.orchestrator.transition_to(SessionState.SESSION_ENDED)
                self.processing_queue.put(("status", "会话已结束 - 可开始新会话"))

                # --- Phase 2: Play farewell TTS synchronously ---
                # Must finish before cleanup so audio isn't killed mid-playback.
                if self.tts_service and full_feedback:
                    try:
                        self.tts_service.generate_and_play(full_feedback)
                    except Exception as e:
                        logger.warning(f"Farewell TTS failed: {e}")

                # --- Phase 3: Generate report + PDF in background ---
                # Report generation is slow (LLM call). Run it after TTS finishes
                # so it doesn't block audio playback or UI exit.
                def _save_report():
                    try:
                        researcher_report = self.report_service.generate_researcher_report(
                            conversation_history, user_id, end_type,
                            user_info=current_user_info, relaxation_info=relaxation_rec or relax_str,
                            session_emotions=self.session_emotions
                        )
                        if isinstance(researcher_report, dict):
                            researcher_report["relaxation_completed"] = True
                            researcher_report["relaxation_type"] = relax_str
                            if self.emotion_tracker:
                                researcher_report["emotion_tracker_data"] = self.emotion_tracker.get_session_emotion_data()
                        if isinstance(researcher_report, dict) and hasattr(self.report_service, 'activity_log'):
                            researcher_report["activity_log"] = self.report_service.activity_log

                        save_result = None
                        if self.data_manager and researcher_report:
                            try:
                                save_result = self.data_manager.save_session_report(
                                    researcher_report, full_feedback, end_type.value
                                )
                            except Exception as e:
                                logger.warning(f"Report save failed: {e}")

                        self._generate_and_save_pdf(researcher_report, user_id, end_type, save_result)

                        if self.data_manager:
                            self.data_manager.save_session_summary(summary=full_feedback[:500])
                    except Exception as e:
                        logger.warning(f"Background report generation failed: {e}")

                threading.Thread(target=_save_report, daemon=True).start()

            except Exception as e:
                logger.exception("Exception occurred")
                self._session_ending = False
                self.session_end_controller.reset()
                logger.warning(f"Report generation failed: {e}")

            finally:
                if self._pending_quit:
                    self._pending_quit = False
                    if self.tts_service:
                        try:
                            self.tts_service.cleanup()
                        except Exception:
                            pass
                    # Use processing_queue — the main thread's QTimer drains it.
                    self.processing_queue.put(("quit", None))

        threading.Thread(target=generate_farewell_and_reports, daemon=True).start()
