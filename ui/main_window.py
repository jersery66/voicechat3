# Main Window - Background, left-right layout, loading transition, queue processing

import os
import sys
import queue
import time
import threading
import traceback
import re
import random
from datetime import datetime

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
)

from .control_panel import ControlPanel
from .chat_panel import ChatPanel
from .loading_screen import LoadingScreen
from .dialogs import (
    CrisisDialog, ContinueOrEndDialog,
    WarningDialog, EndSessionDecisionDialog
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
        self._current_report_generated = False
        self._current_report_generating = False
        self._pipeline_busy = False
        self._completion_status = None
        self._timeout_dialog_open = False
        self._auto_ending_after_relaxation = False
        self._end_decision_open = False
        self._end_request_in_progress = False
        self._pre_end_relax_prompted = False
        self._pending_end_after_video = None

        # Pipeline generation token — used to cancel stale results
        self._pipeline_generation = 0
        self._pipeline_cancel_generation = -1

        # Relaxation-interrupted-scale resume state
        self._scale_interrupted_by_relaxation = False
        self._resume_scale_after_relaxation = None  # {"scale_name": ..., "item": ...}
        self._post_scale_relaxation_recommended = False
        self._post_relaxation_feedback_consumed = False

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

        # Refactor shadow mode: mirror lifecycle decisions into the new
        # app.engine.SessionEngine for validation. LEGACY stays authoritative;
        # engine events are only logged. Never crashes the app: any failure
        # here disables shadow mode silently.
        self.session_engine = None
        try:
            from config import SESSION_ENGINE_SHADOW
            if SESSION_ENGINE_SHADOW:
                from app.engine import SessionEngine
                self.session_engine = SessionEngine(
                    emit=lambda ev: logger.info(
                        f"[EngineShadow] event {ev.kind}: "
                        f"{ev.model_dump(mode='json', exclude={'ts'})}"
                    )
                )
                self.session_engine.start()
                app = QApplication.instance()
                if app is not None:
                    app.aboutToQuit.connect(self.session_engine.shutdown)
                logger.info("[EngineShadow] SessionEngine shadow mode enabled")
        except Exception as e:
            logger.warning(f"[EngineShadow] disabled due to init error: {e}")
            self.session_engine = None

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
        # Re-apply styling to already-created bubbles so old messages recolor.
        for msg in self.chat_panel._messages:
            bubble = msg.get("bubble")
            if bubble is not None:
                bubble.refresh_style()
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
        self.chat_panel.exit_clicked.connect(self._on_exit_program)
        self.chat_panel.end_session_clicked.connect(self._on_end_session)
        self.chat_panel.text_submitted.connect(self._on_text_submitted)

    # ==================== User Info ====================

    def _on_confirm_user(self, info):
        """确认被试信息并开始一个全新的会话。"""
        normalized = self._normalize_user_info(info)
        user_id = normalized.get("user_id", "default_user")

        self.user_info = normalized
        self.info_confirmed = True
        self.current_user_id = user_id

        # Always start a fresh session for each confirmed subject
        self._start_new_session()

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
        """用户点击'修改信息/新会话'：准备下一位，但不播放欢迎语。"""
        self.info_confirmed = False
        # If previous session already ended, clean up now
        if self.orchestrator.state in (SessionState.SESSION_ENDED, SessionState.IDLE):
            self._prepare_next_subject()
        else:
            self.control_panel.set_status("请重新填写信息")

    # ==================== Recording ====================

    def _on_record_started(self):
        if not self.models_loaded:
            self.control_panel.reset_recording()
            return

        if self._pipeline_busy:
            self.chat_panel.add_system_message("正在回复中，请稍等")
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
        # Don't kill relaxation highlight when user starts recording during RELAXATION_RECOMMENDED
        if self.orchestrator.state != SessionState.RELAXATION_RECOMMENDED:
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
            self._pipeline_generation += 1
            gen = self._pipeline_generation
            threading.Thread(target=self._run_pipeline, args=(None, gen), daemon=True).start()
        else:
            self.control_panel.set_status("测试模式 - 服务未加载")
            self.control_panel.reset_recording()

    def _cancel_active_pipeline(self, reason=""):
        """Cancel any in-flight pipeline result to prevent stale TTS/UI updates."""
        self._pipeline_cancel_generation = self._pipeline_generation
        logger.warning(
            f"[PipelineCancel] cancel active pipeline gen={self._pipeline_cancel_generation}, reason={reason}"
        )
        try:
            if self.tts_service:
                self.tts_service.stop_playing()
        except Exception:
            pass

    def _on_text_submitted(self, text):
        """Handle text input from chat panel."""
        if not text.strip():
            return
        if self._pipeline_busy:
            self.chat_panel.add_system_message("正在回复中，请稍等")
            return
        if not self.models_loaded or not self.pipeline:
            self.chat_panel.add_system_message("模型尚未加载完成，请稍候")
            return
        if not self.orchestrator.can_start_pipeline():
            self.chat_panel.add_system_message("当前不在可对话状态，请稍候")
            return
        if not self.info_confirmed:
            self.chat_panel.add_system_message("请先填写左侧基本信息并确认")
            return
        self.processing_queue.put(("status", "正在思考..."))
        self._pipeline_generation += 1
        gen = self._pipeline_generation
        threading.Thread(target=self._run_pipeline, args=(text, gen), daemon=True).start()

    # ==================== Pipeline ====================

    def _run_pipeline(self, text=None, generation=None):
        """Unified pipeline entry point. Runs on a background thread.
        text=None for voice mode (STT+TTS), text=str for text mode.
        generation: monotonically increasing token for stale result suppression."""
        if generation is None:
            generation = self._pipeline_generation
        self._pipeline_busy = True

        def safe_put(mt, ct):
            """Suppress callbacks from stale/cancelled pipeline results."""
            if generation <= self._pipeline_cancel_generation or self._session_ending:
                logger.warning(f"[PipelineCancel] suppress callback {mt} from gen={generation}")
                return
            if self.orchestrator.state != SessionState.CHATTING and mt in (
                "append_chat", "replace_last_ai", "stream_text", "start_ai_message"
            ):
                logger.warning(f"[PipelineCancel] suppress UI callback {mt} in state={self.orchestrator.state}")
                return
            self.processing_queue.put((mt, ct))

        try:
            if not self.orchestrator.can_start_pipeline():
                self.processing_queue.put(("status", "当前不在可对话状态，请稍候"))
                return

            # Post-relaxation feedback: consume first user message, then resume scale
            if self._scale_interrupted_by_relaxation and not self._post_relaxation_feedback_consumed:
                if text is not None:
                    user_text = text
                else:
                    audio_data = self.stt_service.stop_recording()
                    if len(audio_data) == 0:
                        self.processing_queue.put(("status", "未检测到语音"))
                        return
                    user_text = self.stt_service.transcribe(audio_data)
                    if not user_text.strip():
                        self.processing_queue.put(("status", "无法识别内容"))
                        return

                self._post_relaxation_feedback_consumed = True
                logger.warning(f"[RelaxResume] consume post-relaxation feedback: {user_text!r}")

                # Restore the interrupted scale and get the question phrasing
                resume_info = self._resume_scale_after_relaxation
                natural_q = ""
                if resume_info and self.pipeline:
                    natural_q = self.pipeline.restore_active_scale(
                        resume_info["scale_name"], resume_info["item"]
                    ) or ""

                # Acknowledge feedback and prompt to continue with the actual question
                if natural_q:
                    ack_text = f"好，那我们继续把刚才没问完的几个问题补完。{natural_q}"
                else:
                    ack_text = "好，那我们继续把刚才没问完的几个问题补完。"
                # Thread-safe UI updates via queue
                self.processing_queue.put(("append_chat", ("ai", ack_text)))
                self._play_tts_async(ack_text)
                self.processing_queue.put(("status", "继续量表采样..."))
                self._scale_interrupted_by_relaxation = False
                self._resume_scale_after_relaxation = None
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
                    logger.warning("[ASRDebug] audio_data empty after stop_recording")
                    self.processing_queue.put(("status", "未检测到语音"))
                    return

                # ASR debug: check audio quality
                import numpy as np
                from config import SAMPLE_RATE
                duration = len(audio_data) / SAMPLE_RATE
                rms = float(np.sqrt(np.mean(audio_data.astype(np.float64) ** 2)))
                peak = float(np.max(np.abs(audio_data)))
                logger.warning(
                    f"[ASRDebug] captured: samples={len(audio_data)}, "
                    f"duration={duration:.2f}s, rms={rms:.6f}, peak={peak:.6f}"
                )
                if duration < 0.3 or peak < 0.003:
                    logger.warning("[ASRDebug] audio too weak or too short")
                    self.processing_queue.put(("status", "声音太小或未检测到有效语音"))
                    return
                extra = getattr(self, '_pending_scale_prompt', '') or ''
                self._pending_scale_prompt = None
                config = PipelineConfig(use_stt=True, use_tts=True, audio_data=audio_data, extra_system_suffix=extra)

            result = self.pipeline.execute(config, safe_put)
            # Cumulate scale tags — don't overwrite previous rounds' scores
            if result.scale_tags:
                for scale_name, answers in result.scale_tags.items():
                    self._scale_tags.setdefault(scale_name, {})
                    self._scale_tags[scale_name].update(answers)

            # Drop stale pipeline results (user clicked end/relaxation during processing)
            if generation <= self._pipeline_cancel_generation or self._session_ending:
                logger.warning(
                    f"[PipelineCancel] drop stale pipeline result gen={generation}, "
                    f"cancel_gen={self._pipeline_cancel_generation}, state={self.orchestrator.state}"
                )
                return
            if self.orchestrator.state != SessionState.CHATTING:
                logger.warning(
                    f"[PipelineCancel] drop result in non-chatting state: {self.orchestrator.state}"
                )
                return

            self._post_pipeline_routing(result)

        except Exception as e:
            logger.exception("Exception occurred")
            err_msg = str(e)
            if "cuda" in err_msg.lower() or "buffer" in err_msg.lower() or "terminated" in err_msg.lower():
                self.processing_queue.put(("error", "模型显存不足，正在自动恢复，请稍后再试"))
            else:
                self.processing_queue.put(("error", f"处理出错: {err_msg}"))
        finally:
            self._pipeline_busy = False
            if text is not None:
                self.processing_queue.put(("set_buttons_state", "normal"))

    def _post_pipeline_routing(self, result):
        """Route pipeline result to appropriate actions.

        Runs on the pipeline worker thread — only put state to the GUI thread
        via the processing queue. Never create/exec a QDialog here; that must
        happen on the GUI thread to avoid "parent in a different thread" crashes.
        """
        if result.end_type:
            et = get_end_type_enum(result.end_type)
            # Hand off the end request to the GUI thread (consistent with the
            # "auto_end_session" path). SAFETY is handled directly on the GUI
            # thread; other END tags go through the unified readiness check.
            self.processing_queue.put(("end_session_request", (et, result.relaxation_rec)))
            return

        if result.relaxation_rec:
            # Pipeline synced relaxation with spoken_text — safe to highlight
            self.processing_queue.put(("highlight_relax_delayed", (result.relaxation_rec, 500)))
            self.processing_queue.put(("status", "可以尝试左侧放松训练"))
        elif result.all_scales_completed:
            self.processing_queue.put(("all_scales_completed", None))
        elif result.intent == "entertainment":
            self.processing_queue.put(("highlight_relax", "game"))
            self.processing_queue.put(("status", "准备就绪"))
        elif self._should_soft_recommend_relaxation(result):
            tag = self._get_end_relaxation_tag()
            self.processing_queue.put(("highlight_relax", tag))
            self.processing_queue.put(("status", "可以尝试左侧放松训练"))
        else:
            self.processing_queue.put(("status", "准备就绪"))

    # ==================== Queue Processing ====================

    def process_queue(self):
        # VAD auto-stop polling
        if (self.is_recording and self.stt_service
                and self.stt_service.is_vad_triggered()):
            logger.warning("[ASRDebug] VAD triggered, stopping recording")
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

                elif msg_type == "end_session_request":
                    et, relaxation_rec = content
                    if et == EndType.SAFETY:
                        self._handle_session_end(et, relaxation_rec)
                    else:
                        self._request_end_with_readiness_check(et, source="model_end_tag")

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

                elif msg_type == "highlight_relax_delayed":
                    tag, delay_ms = content
                    relax_map = {
                        "呼吸": "breathing", "肌肉": "muscle", "冥想": "meditation", "游戏": "game",
                        "breathing": "breathing", "muscle": "muscle", "meditation": "meditation", "game": "game",
                    }
                    relax_key = relax_map.get(tag, tag)
                    QTimer.singleShot(delay_ms, lambda rk=relax_key: self._highlight_relax_safe(rk))

                elif msg_type == "replace_greeting":
                    self._replace_greeting(content)

                elif msg_type == "replace_last_system":
                    self._replace_last_system(content)

                elif msg_type == "all_scales_completed":
                    self._handle_scales_completed_recommend_relaxation()

                elif msg_type == "auto_end_session":
                    self._request_end_with_readiness_check(content, allow_force_relaxation=False, source="auto_end_after_relaxation")

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
                    if isinstance(content, tuple):
                        relaxation_type, completed = content
                    else:
                        # Compatibility with queues created by older callers.
                        relaxation_type, completed = content, True
                    QTimer.singleShot(
                        500,
                        lambda t=relaxation_type, ok=completed: self._on_video_finished(t, ok),
                    )

                elif msg_type == "game_finished":
                    completed = content if isinstance(content, bool) else True
                    QTimer.singleShot(500, lambda ok=completed: self._on_game_finished(ok))

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

                elif msg_type == "session_finished":
                    self._on_session_finished(
                        report_ok=content if isinstance(content, bool) else True
                    )

                elif msg_type == "quit":
                    self._force_quit_now()
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
                from config import ENABLE_TTS
                if not ENABLE_TTS:
                    self.tts_service = None
                    logger.warning("TTS disabled by config; continuing without TTS.")
                    return
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

    def _prepare_next_subject(self):
        """Clean up after session end — prepare for next subject without creating a new session.

        Resets UI and in-memory state but does NOT call data_manager.start_new_session()
        or report_service.start_session(). Those are deferred to _start_new_session()
        which runs only when the next subject confirms their info.
        """
        self.chat_panel.clear_chat()
        self.session_emotions = []
        self._scale_tags = {}
        self._session_ending = False
        self._pending_quit = False
        self._pipeline_busy = False
        self._end_decision_open = False
        self._end_request_in_progress = False
        self._pre_end_relax_prompted = False
        self._pending_end_after_video = None
        self._completion_status = None
        self._auto_ending_after_relaxation = False
        self._scale_interrupted_by_relaxation = False
        self._resume_scale_after_relaxation = None
        self._post_relaxation_feedback_consumed = False
        self._post_scale_relaxation_recommended = False
        self.current_user_id = None
        self.user_info = {}
        self.info_confirmed = False
        self.control_panel.reset_form()
        self.session_end_controller.reset()
        if hasattr(self, 'emotion_tracker') and self.emotion_tracker:
            self.emotion_tracker.reset()
        if hasattr(self, 'pipeline') and self.pipeline:
            self.pipeline.reset_session()
        if self.llm_service:
            self.llm_service.reset_conversation(clear_context=True)
        # Keep orchestrator in a terminal state (IDLE/SESSION_ENDED) so
        # _on_exit_program() knows there is no active session.
        if self.orchestrator.state not in (SessionState.IDLE, SessionState.SESSION_ENDED):
            self.orchestrator.transition_to(SessionState.SESSION_ENDED)

    def _engine_submit(self, command):
        """Shadow-mode forward of a lifecycle command to SessionEngine.

        Legacy flow remains authoritative; this only validates the engine
        mirror in parallel. Any failure is logged and swallowed so shadow
        mode can never break the running session.
        """
        engine = getattr(self, "session_engine", None)
        if engine is None:
            return
        try:
            engine.submit(command)
        except Exception as e:
            logger.warning(f"[EngineShadow] submit failed: {e}")

    def _start_new_session(self):
        self.chat_panel.clear_chat()
        # Shadow-mode mirror of the session start (legacy stays authoritative)
        try:
            from app.contracts import StartSessionCommand, SubjectInfo
            self._engine_submit(StartSessionCommand(
                subject=SubjectInfo(subject_id=str(self.current_user_id or "unknown"))
            ))
        except Exception as e:
            logger.warning(f"[EngineShadow] start_session forward failed: {e}")
        self.session_emotions = []
        self._scale_tags = {}
        self._session_ending = False
        self._asking_scales = False
        self._user_explicit_end = False
        self._pending_scale_prompt = None
        self._current_report_generated = False
        self._current_report_generating = False
        self._pipeline_busy = False
        self._completion_status = None
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

    def _show_crisis_dialog(self, risk_data=None):
        risk_level = risk_data.get("risk_level", 0) if risk_data else 0
        indicators = risk_data.get("indicators", []) if risk_data else []
        dialog = CrisisDialog(self, CRISIS_HOTLINES, risk_level, indicators)
        dialog.exec()

    def _highlight_relax_safe(self, relax_key: str):
        """Highlight relaxation button with logging and safety checks."""
        logger.warning(f"[UIDebug] highlight_relax_safe called: {relax_key}")
        try:
            self.control_panel.set_buttons_enabled(True)
            self.control_panel.highlight_relax_button(relax_key)
            self.control_panel.set_status("可以尝试左侧放松训练")
        except Exception as e:
            logger.warning(f"[UIDebug] highlight_relax failed: {e}")

    def _handle_scales_completed_recommend_relaxation(self):
        """After all scales are done, recommend relaxation — not end session."""
        if self._post_scale_relaxation_recommended:
            return
        if self.orchestrator.ctx.current_relaxation_type:
            return
        if self.orchestrator.state == SessionState.RELAXATION_RECOMMENDED:
            return

        self._post_scale_relaxation_recommended = True

        tag = self._get_end_relaxation_tag() or "breathing"
        tag_cn = {"breathing": "呼吸", "muscle": "肌肉", "meditation": "冥想"}.get(tag, "呼吸")

        display_text = (
            f"刚才我们已经把你最近这段时间的状态大致了解清楚了。"
            f"你前面提到的这些感受，身体上也可能会跟着紧绷。"
            f"先不用急着结束，可以做一个短的{tag_cn}放松训练，让身体缓一缓。"
            f"你可以点左侧的{tag_cn}放松训练，跟着做几分钟。"
        )
        tts_text = (
            f"刚才我们已经把你最近这段时间的状态大致了解清楚了。[breath]"
            f"你前面提到的这些感受，身体上也可能会跟着紧绷。[breath]"
            f"先不用急着结束，可以做一个短的{tag_cn}放松训练，让身体缓一缓。"
        )

        self.chat_panel.add_system_message(display_text, as_ai=True)
        self._play_tts_async(tts_text)

        self.orchestrator.transition_to(SessionState.RELAXATION_RECOMMENDED)
        self.processing_queue.put(("highlight_relax_delayed", (tag, 500)))
        self.control_panel.set_status("建议完成放松训练")

    def _cancel_post_relaxation_timer(self):
        """取消放松后超时定时器（用户做出选择或关闭弹窗时调用）。

        保留为安全空操作：当前放松后不自动结束，定时器不再创建。
        """
        if hasattr(self, '_post_relaxation_timer') and self._post_relaxation_timer.isActive():
            self._post_relaxation_timer.stop()

    def _play_tts_then_auto_end(self, text, end_type):
        """Play a short auto-end notice, then enter the full session-end flow."""
        def runner():
            try:
                if self.tts_service and text:
                    self.tts_service.generate_and_play(text)
            except Exception as e:
                logger.warning(f"Auto-end notice TTS failed: {e}")
            finally:
                self.processing_queue.put(("auto_end_session", end_type))

        threading.Thread(target=runner, daemon=True).start()

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
        """统一的放松视频播放流程：全屏播放 → 弹窗
        relaxation_type: 'breathing', 'muscle', 'meditation'
        """
        if not self.orchestrator.can_play_video():
            return

        # Shadow-mode mirror (legacy stays authoritative)
        try:
            from app.contracts import PlayRelaxationCommand
            self._engine_submit(PlayRelaxationCommand(relaxation=relaxation_type))
        except Exception as e:
            logger.warning(f"[EngineShadow] play_relaxation forward failed: {e}")

        # Record scale state before relaxation interrupts it
        if self.pipeline:
            active_state = self.pipeline.get_active_scale_state()
            if active_state:
                self._scale_interrupted_by_relaxation = True
                self._resume_scale_after_relaxation = active_state
                self._post_relaxation_feedback_consumed = False
                logger.warning(f"[RelaxResume] relaxation will interrupt scale: {active_state}")

        self.orchestrator.transition_to(SessionState.VIDEO_PLAYING)
        self.control_panel.stop_all_blinks()

        # Store type for recording after video finishes (not before — video may fail)
        self._pending_relaxation_type = relaxation_type

        def video_runner():
            completed = False
            try:
                completed = bool(self.video_tool.execute(relaxation_type=relaxation_type))
            except Exception as e:
                logger.warning(f"Video error: {e}")
            finally:
                self.processing_queue.put(("video_finished", (relaxation_type, completed)))

        threading.Thread(target=video_runner, daemon=True).start()

    def _on_video_finished(self, relaxation_type, completed=True):
        """视频播放完成：记录放松，回到正常聊天。不弹结束框。"""
        # A missing or failed video is not a completed intervention and must
        # never be recorded as one in either the shadow engine or the report.
        try:
            from app.contracts import RelaxationFinishedCommand
            self._engine_submit(RelaxationFinishedCommand(completed=completed))
        except Exception as e:
            logger.warning(f"[EngineShadow] relaxation_finished forward failed: {e}")
        # Record relaxation AFTER video finishes
        relax_name = ""
        if completed:
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

        # Return to normal chat (must go through POST_RELAXATION first)
        self.orchestrator.transition_to(SessionState.POST_RELAXATION)
        self.orchestrator.transition_to(SessionState.CHATTING)
        self._end_decision_open = False
        self._pre_end_relax_prompted = False

        # Check if end was deferred while video was playing
        pending_end = getattr(self, "_pending_end_after_video", None)
        if pending_end:
            self._pending_end_after_video = None
            if not completed:
                self.chat_panel.add_system_message("放松训练未能启动，未记录为已完成。请检查媒体文件后重试。")
                self.control_panel.set_status("放松训练未完成")
                return
            logger.info(f"[EndFlow] executing deferred end after video: {pending_end}")
            self._request_end_with_readiness_check(
                pending_end, allow_force_relaxation=False, source="after_video"
            )
            return

        # If a scale was interrupted by relaxation, set up for resume
        if self._scale_interrupted_by_relaxation and self._resume_scale_after_relaxation:
            # Ask for relaxation feedback first — don't resume scale yet
            self._post_relaxation_feedback_consumed = False
            message = "刚才这个练习先到这里，你现在身体有没有稍微松一点？"
            self.chat_panel.add_system_message(message)
            self._play_tts_async(message)
            self.control_panel.set_status("等待反馈后继续...")
        else:
            # Normal continuation
            message = random.choice(POST_RELAXATION_MESSAGE) if POST_RELAXATION_MESSAGE else "刚才这个练习先到这里，你可以感受一下现在身体有没有稍微松一点。我们可以继续聊。"
            self.chat_panel.add_system_message(message)
            self._play_tts_async(message)
            self.control_panel.set_status("继续对话中...")

    def _play_game(self):
        if not self.orchestrator.can_play_video():
            return
        self.orchestrator.transition_to(SessionState.VIDEO_PLAYING)
        self.control_panel.stop_all_blinks()

        def game_runner():
            completed = False
            try:
                from services.game_service import get_game_service
                game = get_game_service()
                game_result = game.launch()
                completed = bool(game_result.get("_completed", True)) if isinstance(game_result, dict) else False
            except Exception as e:
                logger.warning(f"Game error: {e}")
            finally:
                self.processing_queue.put(("game_finished", completed))

        threading.Thread(target=game_runner, daemon=True).start()

    def _on_game_finished(self, completed: bool = True):
        """Game finished — return to normal chat. No dialog, no end prompt."""
        # Record relaxation
        if completed and self.report_service:
            self.report_service.record_relaxation("game")
            self.report_service.activity_log.append({
                "type": "relaxation",
                "relaxation_type": "game",
                "relaxation_name": "game",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })

        # Return to normal chat (must go through POST_RELAXATION first)
        self.orchestrator.transition_to(SessionState.POST_RELAXATION)
        self.orchestrator.transition_to(SessionState.CHATTING)
        self._end_decision_open = False
        self._pre_end_relax_prompted = False

        if completed:
            message = random.choice(POST_RELAXATION_MESSAGE) if POST_RELAXATION_MESSAGE else "游戏结束了，感觉怎么样？"
            status = "继续对话中..."
        else:
            message = "小游戏未能完成，未记录为已完成的放松训练。我们可以继续聊。"
            status = "小游戏未完成"
        self.chat_panel.add_system_message(message)
        self._play_tts_async(message)
        self.control_panel.set_status(status)

    def _ask_continue_or_end(self):
        """Ask user whether to continue or end when time limit is reached."""
        # Guard against duplicate dialogs from queue
        if getattr(self, "_timeout_dialog_open", False):
            return
        if self.orchestrator.state in (SessionState.SESSION_ENDING, SessionState.SESSION_ENDED):
            return

        self._timeout_dialog_open = True
        try:
            from ui.dialogs import ContinueOrEndDialog
            dialog = ContinueOrEndDialog(parent=self, timeout=True)
            dialog.continue_chosen.connect(self._on_timeout_continue)
            def _on_end():
                self._user_explicit_end = True
                self._handle_session_end(EndType.TIME_LIMIT)
            dialog.end_chosen.connect(_on_end)
            dialog.exec()
        finally:
            self._timeout_dialog_open = False

    def _on_timeout_continue(self):
        """User chose to continue after time limit."""
        # Shadow-mode mirror: engine must never re-ask the time limit this
        # session (legacy continued_after_time_limit parity).
        try:
            from app.contracts import AcknowledgeTimeLimitCommand
            self._engine_submit(AcknowledgeTimeLimitCommand())
        except Exception as e:
            logger.warning(f"[EngineShadow] acknowledge_time_limit forward failed: {e}")
        if self.report_service:
            self.report_service.continued_after_time_limit = True
            self.report_service.time_limit_prompt_shown = True
            self.report_service.time_warning_shown = True
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
                        "你是小薇，一位温暖亲切的心理咨询师。请生成一句简短欢迎问候语（不超过30字），口语化有温度像老朋友打招呼。只输出问候语本身。",
                        max_tokens=60
                    )
            except Exception as e:
                logger.debug(f"Greeting generation failed: {e}")
            if generated and len(generated.strip()) > 5:
                generated = generated.strip()
                if len(generated) > 60:
                    generated = generated[:60]
                self.processing_queue.put(("replace_greeting", generated))
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
                        f"你是小薇。来访者刚完成{relax_name}训练，生成一句简短关心问候（不超过25字）。只输出问候语本身。",
                        max_tokens=50
                    )
            except Exception as e:
                logger.debug(f"Post-relaxation greeting failed: {e}")
            if generated and len(generated.strip()) > 3:
                generated = generated.strip()
                if len(generated) > 50:
                    generated = generated[:50]
                self.processing_queue.put(("replace_last_system", generated))
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
                        "你是小薇。生成一句简短的话引导来访者填写左边的基本信息并点确认（不超过30字）。只输出这句话本身。",
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

    def _stop_ollama_keepalive(self):
        """Stop keepalive during session ending/report generation."""
        timer = getattr(self, "_keepalive_timer", None)
        if timer and timer.isActive():
            timer.stop()
            logger.info("[KeepAlive] stopped during session ending")

    def _ollama_keepalive_tick(self):
        if self._session_ending or self._current_report_generating:
            return
        if self.orchestrator.state != SessionState.CHATTING:
            return
        if self.llm_service:
            threading.Thread(
                target=self.llm_service.warmup, daemon=True
            ).start()

    # ==================== Actions ====================

    def _clear_history(self):
        self._start_new_session()
        self._play_opening_greeting()

    def _on_exit_program(self):
        """退出整个程序。如果当前被试报告未生成，先生成报告再退出。

        组合守卫：退出意图始终记录在 `self._pending_quit`，并在已有结束流程
        （正在结束 / 决策弹窗打开 / 请求进行中）时不重复发起新的结束请求，
        避免退出意图被静默丢失。已有流程完成时会据此自动退出。
        """
        self._cancel_active_pipeline(reason="exit program")
        # Mark quit intent up front so it survives any in-progress end flow.
        self._pending_quit = True

        if self._session_ending or self._end_decision_open or self._end_request_in_progress:
            # An end flow is already running/showing a dialog — don't stack
            # another request; just wait. It will auto-quit when finished.
            logger.info("[EndFlow] quit requested during end flow; waiting for completion")
            if not getattr(self, "_exit_wait_dialog", None):
                self._show_exit_waiting_dialog(
                    "正在保存本次会话，完成后将自动退出...", force_quit_timeout=120000
                )
            return

        # No current subject — just quit immediately, no report needed.
        no_current_subject = (
            not self.info_confirmed
            or not self.current_user_id
            or self.orchestrator.state in (SessionState.IDLE, SessionState.SESSION_ENDED)
        )
        if no_current_subject:
            self._force_quit_now()
            return

        has_active_session = (
            self.models_loaded
            and self.orchestrator.state not in (SessionState.SESSION_ENDED, SessionState.IDLE)
        )

        if has_active_session and not self._current_report_generated:
            # Use unified end flow — will check scales, relaxation, then end.
            self._user_explicit_end = True
            self._request_end_with_readiness_check(
                EndType.QUIT, allow_force_relaxation=False, source="exit_program"
            )
            return

        self._force_quit_now()

    def _on_end_session(self):
        """结束当前被试会话：先检查完成度，再弹窗让用户选择。"""
        if self._session_ending or self._end_decision_open or self._end_request_in_progress:
            logger.info("[EndFlow] duplicate end click ignored")
            return
        self._cancel_active_pipeline(reason="end session requested")

        if not self.models_loaded or self.orchestrator.state in (
            SessionState.SESSION_ENDED, SessionState.IDLE
        ):
            self.chat_panel.add_system_message("当前没有进行中的会话。请填写参与者信息后开始。")
            return

        self._end_request_in_progress = True
        self._request_end_with_readiness_check(EndType.GOAL_ACHIEVED, source="user_button")

    def _request_end_with_readiness_check(self, end_type, allow_force_relaxation=True, source="unknown"):
        """Unified end flow: check readiness before ending.

        All end paths should go through this method:
        - auto-end, time limit, user click, model END tag, relaxation timeout

        Checks:
        1. VIDEO_PLAYING → defer end until video finishes
        2. Incomplete scales → prompt to continue or end
        3. No relaxation done → recommend once (non-blocking)
        4. Then proceed to actual end
        """
        logger.info(f"[EndFlow] _request_end_with_readiness_check: end_type={end_type}, source={source}")

        # If video is playing, defer end until it finishes
        if self.orchestrator.state == SessionState.VIDEO_PLAYING:
            self._pending_end_after_video = end_type
            self.control_panel.set_status("放松训练结束后将自动完成会话")
            logger.info("[EndFlow] deferred end until video finishes")
            return

        # If scale was interrupted by relaxation and not yet resumed, block end
        if self._scale_interrupted_by_relaxation and not self._post_relaxation_feedback_consumed:
            if source != "direct_end_confirmed":
                # Restore scale and ask the question directly
                if self._resume_scale_after_relaxation and self.pipeline:
                    self.pipeline.restore_active_scale(
                        self._resume_scale_after_relaxation["scale_name"],
                        self._resume_scale_after_relaxation["item"]
                    )
                    self._scale_interrupted_by_relaxation = False
                    self._resume_scale_after_relaxation = None

                    natural_q = self.pipeline.get_active_scale_question_text()
                    if natural_q:
                        msg = f"还剩几个问题没补完，我们先把这个问完。{natural_q}"
                    else:
                        msg = "还剩几个问题没补完，我们再问几个很短的问题就结束。"
                    self.chat_panel.add_system_message(msg)
                    self._play_tts_async(msg)
                    self.control_panel.set_status("继续量表采样...")
                self._end_request_in_progress = False
                return

        # Get readiness state
        state = self._get_end_readiness_state()
        scale_incomplete = state["scale_incomplete"]
        relax_done = state["relax_done"]

        # Store pending end info for use in _end_session_directly
        self._pending_end_type = end_type
        self._pending_end_allow_force = allow_force_relaxation
        self._pending_end_source = source

        # If scales incomplete, always prompt (except when user already confirmed direct end)
        if scale_incomplete and source != "direct_end_confirmed":
            self._show_end_decision_dialog(state)
            return

        # If no relaxation done, recommend once (non-blocking, no TTS to avoid overlap)
        if not relax_done and source not in ("auto_end_after_relaxation", "direct_end_confirmed"):
            tag = self._get_end_relaxation_tag()
            tag_cn = {"breathing": "呼吸", "muscle": "肌肉", "meditation": "冥想"}.get(tag, "呼吸")

            if not self._pre_end_relax_prompted:
                self._pre_end_relax_prompted = True
                rec_text = f"结束前要不要先做个短的{tag_cn}放松？左边有按钮，做完咱们再结束。"
                self.chat_panel.add_system_message(rec_text, as_ai=True)
                # Don't play TTS — it overlaps with farewell TTS if user clicks "direct end"
                self.processing_queue.put(("highlight_relax_delayed", (tag, 300)))

            self._show_end_decision_dialog(state)
            return

        # All checks passed — proceed to end directly
        self._handle_session_end(end_type, allow_force_relaxation=allow_force_relaxation)

    def _get_end_readiness_state(self):
        """Return completion state before ending current subject session."""
        incomplete_scales = []
        if self.pipeline and hasattr(self.pipeline, "get_incomplete_scales"):
            try:
                incomplete_scales = self.pipeline.get_incomplete_scales()
            except Exception:
                incomplete_scales = []

        scale_incomplete = bool(incomplete_scales)
        relax_done = self._get_relaxation_info_str() not in ("未进行",)

        return {
            "scale_incomplete": scale_incomplete,
            "incomplete_scales": incomplete_scales,
            "relax_done": relax_done,
            "relax_type": self._get_relaxation_info_str(),
        }

    def _show_end_decision_dialog(self, state):
        """Show end-session decision dialog based on completion state."""
        if self._end_decision_open:
            logger.info("[EndFlow] decision dialog already open; skip")
            return

        self._end_decision_open = True
        self._end_request_in_progress = False
        self.control_panel.set_buttons_enabled(False)

        try:
            from .dialogs import EndSessionDecisionDialog
            recommended = self._get_end_relaxation_tag()
            dialog = EndSessionDecisionDialog(self, state=state, recommended_tag=recommended)
            dialog.continue_chosen.connect(self._end_session_continue_chat)
            dialog.relax_chosen.connect(self._end_session_with_relaxation)
            dialog.end_chosen.connect(lambda: self._end_session_directly(state))
            dialog.cancel_chosen.connect(lambda: None)
            dialog.exec()
        finally:
            self._end_decision_open = False
            if not self._session_ending and self.orchestrator.state not in (
                SessionState.SESSION_ENDING, SessionState.SESSION_ENDED
            ):
                self.control_panel.set_buttons_enabled(True)

    def _end_session_continue_chat(self):
        """User chose to continue — enter force-complete scales mode."""
        self._force_complete_scales = True
        self._session_ending = False
        self._end_request_in_progress = False

        # Transition to CHATTING (handle already-in-CHATTING gracefully)
        try:
            self.orchestrator.transition_to(SessionState.CHATTING)
        except Exception:
            pass  # already in CHATTING

        incomplete = []
        if self.pipeline and hasattr(self.pipeline, "get_incomplete_scales"):
            try:
                incomplete = self.pipeline.get_incomplete_scales()
            except Exception:
                pass

        if incomplete:
            # Force-resume the first incomplete scale and ask directly
            first = incomplete[0]
            scale_name = first["scale_name"]
            remaining = first.get("remaining_nums", [])
            if remaining:
                # Restore the scale and get the question phrasing
                self.pipeline.force_resume_incomplete_scale()
                natural_q = self.pipeline.get_active_scale_question_text()
                if natural_q:
                    msg = f"好，那我们继续把剩下的补完。{natural_q}"
                else:
                    msg = "好，那我们再聊一会儿，把刚才没问完的问题慢慢补上。"
                self.chat_panel.add_system_message(msg)
                self._play_tts_async(msg)
                self.control_panel.set_buttons_enabled(True)
                self.control_panel.set_status("继续补完量表...")
                return
            else:
                msg = "好，咱们继续聊。"
        else:
            msg = "好，咱们继续聊。"

        self.chat_panel.add_system_message(msg)
        self._play_tts_async(msg)
        self.control_panel.set_buttons_enabled(True)
        self.control_panel.set_status("继续对话中...")

    def _should_soft_recommend_relaxation(self, result):
        """Check if relaxation should be softly recommended based on emotion/symptoms."""
        if self.orchestrator.state != SessionState.CHATTING:
            return False
        if self.orchestrator.ctx.current_relaxation_type:
            return False
        # Don't recommend relaxation too early in the conversation
        from config import MIN_ROUNDS_FOR_RELAXATION
        current_rounds = self.report_service.get_round_count() if self.report_service else 0
        if current_rounds < MIN_ROUNDS_FOR_RELAXATION:
            return False

        text = result.user_text or ""
        emotion = result.emotion_result.get("emotion", "")
        intensity = result.emotion_result.get("intensity", 0)

        keywords = [
            "睡不着", "失眠", "紧张", "焦虑", "烦躁", "心慌",
            "喘不过气", "身体很累", "很累", "没力气", "不耐烦",
        ]
        return (
            any(k in text for k in keywords)
            or emotion in {"anxious", "stressed", "angry"}
            or intensity >= 0.75
        )

    def _get_end_relaxation_tag(self):
        """Get the recommended relaxation tag for end-session dialog."""
        if self.orchestrator.ctx.current_relaxation_type:
            type_map = {"huxi": "breathing", "jirou": "muscle", "mingxiang": "meditation"}
            return type_map.get(self.orchestrator.ctx.current_relaxation_type, "breathing")
        if self.relaxation_tool:
            try:
                conversation_history = self.llm_service.conversation_history
                tag = self.relaxation_tool.execute(conversation_history=conversation_history)
                tag_map = {"呼吸": "breathing", "肌肉": "muscle", "冥想": "meditation"}
                return tag_map.get(tag, "breathing")
            except Exception:
                pass
        return "breathing"

    def _end_session_directly(self, state=None):
        """User chose 'end directly' in the decision dialog — confirmed direct end."""
        self._user_explicit_end = True

        # Stop pre-end recommendation TTS before farewell TTS starts
        try:
            if self.tts_service:
                self.tts_service.stop_playing()
        except Exception:
            pass

        # Store completion_status for report
        if state:
            self._completion_status = {
                "scale_completed": not state["scale_incomplete"],
                "incomplete_scales": state["incomplete_scales"],
                "relaxation_completed": state["relax_done"],
                "relaxation_type": state["relax_type"],
                "ended_by_user": True,
            }
        else:
            self._completion_status = {"ended_by_user": True}

        # Use pending end info from _request_end_with_readiness_check
        end_type = getattr(self, '_pending_end_type', EndType.GOAL_ACHIEVED)
        allow_force = getattr(self, '_pending_end_allow_force', False)
        source = getattr(self, '_pending_end_source', '')

        if source == "exit_program":
            self._pending_quit = True
            self._show_exit_waiting_dialog("正在保存本次会话，完成后将自动退出...", force_quit_timeout=120000)
        else:
            self._show_exit_waiting_dialog("会话结束，感谢你的参与，请稍候...")

        # Call _handle_session_end with source="direct_end_confirmed" to skip re-checking
        self._handle_session_end(end_type, allow_force_relaxation=allow_force)

    def _end_session_with_relaxation(self):
        """User chose 'do relaxation first' — recommend training, no report yet."""
        self._cancel_active_pipeline(reason="end with relaxation chosen")
        self._pending_quit = False
        self._user_explicit_end = False

        tag = self._get_end_relaxation_tag()
        tag_cn = {"breathing": "呼吸", "muscle": "肌肉", "meditation": "冥想"}.get(tag, "呼吸")
        rec_text = f"行，结束前咱们先做个短放松。[breath]你可以点左边的{tag_cn}放松训练。"
        self.chat_panel.add_system_message(rec_text, as_ai=True)
        self._play_tts_async(rec_text)

        self.orchestrator.transition_to(SessionState.RELAXATION_RECOMMENDED)
        QTimer.singleShot(1000, lambda: self.control_panel.highlight_relax_button(tag))
        self.control_panel.set_status("请先完成放松训练")

    def _on_session_finished(self, report_ok: bool = True):
        """Session ended — keep chat visible until operator confirms next subject."""
        if self._exit_wait_dialog:
            self._exit_wait_dialog.close()
            self._exit_wait_dialog = None
        self._session_ending = False
        self._current_report_generating = False
        self._current_report_generated = report_ok
        # Disable recording but keep chat and farewell visible
        self.control_panel.set_buttons_enabled(False)
        if report_ok:
            self.control_panel.set_status("会话已结束，报告已保存")
            self.chat_panel.add_system_message(
                '会话已结束，报告已保存。点击"确认信息并开始"进入下一位参与者。'
            )
        else:
            self.control_panel.set_status("会话已结束，报告保存失败")
            self.chat_panel.add_system_message(
                '会话已结束，但报告保存失败；请检查数据目录和日志后再开始下一位参与者。'
            )

    def _force_quit_now(self):
        """Immediate quit — stop all services and exit, no farewell/report."""
        import os as _os
        self._session_ending = True
        # Stop timers
        for timer_name in ("_queue_timer", "_progress_timer", "_keepalive_timer", "_post_relaxation_timer"):
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
        # Close exit waiting dialog
        if self._exit_wait_dialog:
            try:
                self._exit_wait_dialog.close()
            except Exception:
                pass
            self._exit_wait_dialog = None
        QApplication.quit()
        # Hard fallback: torch/sounddevice/ollama threads may keep process alive
        QTimer.singleShot(2000, lambda: _os._exit(0))

    def _show_exit_waiting_dialog(self, message="正在处理，请稍候...", force_quit_timeout=None):
        """Show a non-modal 'processing' dialog until quit/session-end finishes.

        Args:
            force_quit_timeout: ms before os._exit(0). None = no force quit
            (used for "end session" which should never kill the process).
        """
        if hasattr(self, '_exit_wait_dialog') and self._exit_wait_dialog is not None:
            return  # already shown
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtGui import QFont
        import os as _os
        dlg = QDialog(self)
        dlg.setWindowTitle("请稍候")
        dlg.setModal(False)  # non-modal: event loop keeps running
        dlg.setMinimumSize(320, 140)
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowCloseButtonHint)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(30, 25, 30, 25)
        label = QLabel(message)
        label.setFont(QFont("Microsoft YaHei", 13))
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        self._exit_wait_dialog = dlg
        dlg.show()
        if force_quit_timeout:
            QTimer.singleShot(force_quit_timeout, lambda: _os._exit(0))

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
        event.ignore()
        self._on_exit_program()

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

    def _handle_session_end(self, end_type, relaxation_tag=None, allow_force_relaxation=True):
        """Handle session end using orchestrator decision logic."""
        # Shadow-mode mirror of the end request (legacy stays authoritative).
        # Forwarded BEFORE the legacy guards consume _user_explicit_end so the
        # engine sees the same effective inputs as the legacy decision.
        try:
            from app.contracts import EndSessionCommand
            self._engine_submit(EndSessionCommand(
                end_type=end_type,
                allow_force_relaxation=bool(
                    allow_force_relaxation and not self._user_explicit_end
                ),
                ai_relaxation_tag=relaxation_tag,
                source="legacy_end_flow",
            ))
        except Exception as e:
            logger.warning(f"[EngineShadow] end_session forward failed: {e}")
        if self._session_ending:
            logger.info("[EndFlow] _handle_session_end ignored: already ending")
            return
        if not self.session_end_controller.begin().accepted:
            return
        self._session_ending = True

        # Stop competing resources immediately
        self._stop_ollama_keepalive()
        if self.tts_service:
            try:
                self.tts_service.stop_playing()
            except Exception:
                pass
        logger.info("[EndFlow] session ending started, keepalive and TTS stopped")

        # If user explicitly chose to end or force relaxation is disabled,
        # skip forced relaxation — respect their choice.
        user_explicit = self._user_explicit_end
        self._user_explicit_end = False  # consume the flag

        # Use orchestrator to decide: force relaxation or generate reports
        if allow_force_relaxation and not user_explicit and not relaxation_tag and not self.orchestrator.ctx.current_relaxation_type:
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

        if allow_force_relaxation:
            # Feed the pipeline's own "已做过放松" flag so the FSM honors the
            # "at most once per session" rule even when relaxation was driven
            # outside the FSM (completes the M10 fix in core/session_fsm.py).
            action, data = self.orchestrator.evaluate_session_end(
                end_type, relaxation_tag,
                relaxation_used=getattr(self.pipeline, "relaxation_used", False),
            )
        else:
            self.orchestrator.transition_to(SessionState.SESSION_ENDING)
            action, data = "generate_reports", {}

        if action == "force_relaxation":
            # Close waiting dialog so user can see/click relaxation buttons
            if self._exit_wait_dialog:
                self._exit_wait_dialog.close()
                self._exit_wait_dialog = None
            tag = data["relaxation_tag"]
            tag_cn_map = {"breathing": "呼吸", "muscle": "肌肉放松", "meditation": "冥想", "呼吸": "呼吸", "肌肉": "肌肉放松", "冥想": "冥想"}
            tag_cn = tag_cn_map.get(tag, tag)
            rec_text = f"等等，在结束之前，我留意到你还是有点紧张。要不咱们先做个{tag_cn}放松训练？只需几分钟，效果很好的。"
            self.processing_queue.put(("append_chat", ("ai", rec_text)))
            self._play_tts_async(rec_text)
            self.processing_queue.put(("highlight_relax_delayed", (tag, 1000)))
            self.processing_queue.put(("status", "请尝试放松训练"))
            # Defer the end until the user finishes the recommended relaxation.
            # Remember the deferred end so _on_video_finished can re-trigger it;
            # otherwise the session could never end after relaxation completes.
            self._pending_end_after_video = end_type
            self._session_ending = False
            self._end_request_in_progress = False
            self.session_end_controller.defer_for_relaxation()
            return

        # action == "generate_reports"
        is_exit = self._pending_quit
        if is_exit:
            self.processing_queue.put(("status", "正在保存本次会话..."))
        else:
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

                # --- Phase 1: Visitor feedback ---
                # Exit path: skip long farewell, keep it short.
                # End session: generate full farewell via LLM streaming.
                full_feedback = ""
                if is_exit:
                    full_feedback = "本次会话已结束，系统正在保存报告。"
                    self.processing_queue.put(("append_chat", ("ai", full_feedback)))
                else:
                    self.processing_queue.put(("start_ai_message", None))
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

                # --- Phase 2: Save raw snapshot → Generate report + PDF FIRST ---
                # Reports MUST complete before farewell TTS plays.
                self._current_report_generating = True
                self.processing_queue.put(("status", "正在保存会话数据..."))
                raw_snapshot_saved = False
                try:
                    if self.data_manager:
                        self.data_manager.save_session_summary(summary=full_feedback[:500])
                        raw_snapshot_saved = True
                        logger.info("Raw session snapshot saved before report generation")
                except Exception as e:
                    logger.warning(f"Raw snapshot save failed: {e}")

                self.processing_queue.put(("status", "正在生成报告..."))

                # Gather structured scale results for the report
                scale_results = {}
                if self.pipeline and hasattr(self.pipeline, "get_scale_results"):
                    try:
                        scale_results = self.pipeline.get_scale_results()
                    except Exception as e:
                        logger.warning(f"Failed to get scale results: {e}")

                # Generate report with timeout
                researcher_report = None
                report_ok = False
                try:
                    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
                    report_pool = ThreadPoolExecutor(max_workers=1)
                    try:
                        future = report_pool.submit(
                            self.report_service.generate_researcher_report,
                            conversation_history, user_id, end_type,
                            current_user_info, relaxation_rec or relax_str,
                            self.session_emotions, scale_results
                        )
                        researcher_report = future.result(timeout=60)
                    finally:
                        report_pool.shutdown(wait=False, cancel_futures=True)

                    if isinstance(researcher_report, dict):
                        relaxation_done = relax_str not in ("未进行", "", None, "未知")
                        researcher_report["relaxation_completed"] = relaxation_done
                        researcher_report["relaxation_type"] = relax_str if relaxation_done else "未进行"
                        if self.emotion_tracker:
                            researcher_report["emotion_tracker_data"] = self.emotion_tracker.get_session_emotion_data()
                        if scale_results:
                            researcher_report["scale_results"] = scale_results
                    if isinstance(researcher_report, dict) and hasattr(self.report_service, 'activity_log'):
                        researcher_report["activity_log"] = self.report_service.activity_log
                    if isinstance(researcher_report, dict) and self._completion_status:
                        researcher_report["completion_status"] = self._completion_status

                    save_result = None
                    if self.data_manager and researcher_report:
                        try:
                            save_result = self.data_manager.save_session_report(
                                researcher_report, full_feedback, end_type.value
                            )
                        except Exception as e:
                            logger.warning(f"Report save failed: {e}")

                    # PDF with timeout
                    try:
                        pdf_pool = ThreadPoolExecutor(max_workers=1)
                        try:
                            pdf_future = pdf_pool.submit(
                                self._generate_and_save_pdf,
                                researcher_report, user_id, end_type, save_result
                            )
                            pdf_future.result(timeout=30)
                        finally:
                            pdf_pool.shutdown(wait=False, cancel_futures=True)
                    except Exception as e:
                        logger.warning(f"PDF generation timed out or failed: {e}")

                    report_ok = bool(
                        isinstance(researcher_report, dict)
                        and isinstance(save_result, dict)
                        and save_result.get("ok", False)
                    )
                    self._current_report_generated = report_ok
                    if not report_ok:
                        logger.warning(
                            "Report persistence incomplete: save_result=%r", save_result
                        )
                except FuturesTimeout:
                    logger.warning("Report generation timed out (60s)")
                except Exception as e:
                    logger.warning(f"Report generation failed: {e}")
                finally:
                    self._current_report_generating = False

                if not report_ok:
                    msg = "原始数据已保存" if raw_snapshot_saved else "数据保存可能不完整"
                    self.processing_queue.put(("status", f"{msg}，完整报告生成失败/超时，可开始下一位被试"))

            except Exception as e:
                logger.exception("Exception occurred")
                self._session_ending = False
                self.session_end_controller.reset()
                logger.warning(f"Report generation failed: {e}")

            finally:
                # --- Phase 3: Play farewell TTS AFTER report/PDF is done ---
                if not is_exit and full_feedback and self.tts_service:
                    logger.info("[SessionEnd] report/PDF done, now playing farewell TTS")
                    try:
                        self.tts_service.generate_and_play(full_feedback)
                        logger.info("[SessionEnd] farewell TTS finished")
                    except Exception as e:
                        logger.warning(f"[SessionEnd] farewell TTS failed: {e}")

                if is_exit:
                    logger.info("[ExitDebug] report done, queuing quit")
                    self.processing_queue.put(("quit", None))
                else:
                    logger.info("[ExitDebug] report done, queuing session_finished")
                    self.processing_queue.put(("session_finished", report_ok))

        threading.Thread(target=generate_farewell_and_reports, daemon=True).start()
