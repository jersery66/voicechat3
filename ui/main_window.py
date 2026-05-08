# Main Window - Background, left-right layout, loading transition, queue processing

import os
import sys
import queue
import time
import threading
import traceback
import re

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QApplication, QLabel
)
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QFont, QPixmap, QPainter, QColor, QBrush

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    APP_NAME, CRISIS_HOTLINES, GREETING_MESSAGE, GREETING_VARIANTS,
    POST_RELAXATION_MESSAGE, FILL_INFO_PROMPT, TRANSITION_PROMPT,
    SUGGESTIONS_PROMPT, CONTINUE_CHAT_MESSAGE, MIN_ROUNDS_FOR_RELAXATION,
    POST_RELAXATION_TIMEOUT, TIMEOUT_END_MESSAGE, VOICE_PROMPT_PATH, VOICE_PROMPT_TEXT
)

from .control_panel import ControlPanel
from .chat_panel import ChatPanel
from .loading_screen import LoadingScreen
from .dialogs import (
    SessionEndDialog, CrisisDialog, ContinueOrEndDialog,
    WarningDialog
)
from .styles import get_style


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

        # State
        self.is_recording = False
        self.models_loaded = False
        self.session_ended = False
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
        self.control_panel.play_breathing.connect(lambda: self._play_video("呼吸训练.mp4"))
        self.control_panel.play_muscle.connect(lambda: self._play_video("肌肉放松.mp4"))
        self.control_panel.play_meditation.connect(lambda: self._play_video("冥想训练.mp4"))
        self.control_panel.play_game.connect(self._play_game)

        # Chat panel signals
        self.chat_panel.clear_clicked.connect(self._clear_history)
        self.chat_panel.exit_clicked.connect(self._exit_app)
        self.chat_panel.text_submitted.connect(self._on_text_submitted)

    # ==================== User Info ====================

    def _on_confirm_user(self, info):
        self.user_info = info
        self.info_confirmed = True
        user_id = info.get("user_id", "default_user")

        # Check if user ID changed
        if self.current_user_id is not None and user_id != self.current_user_id:
            self._start_new_session()
            self._play_opening_greeting()
            self.control_panel.set_status("用户已更换，请等待问候后再录音")
            return

        if self.current_user_id is None:
            self.current_user_id = user_id

        # Save user info to data manager
        if self.data_manager:
            self.data_manager.set_user_id(user_id)

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
            threading.Thread(target=self._process_pipeline, daemon=True).start()
        else:
            self.control_panel.set_status("测试模式 - 服务未加载")
            self.control_panel.reset_recording()

    def _on_text_submitted(self, text):
        """Handle text input from chat panel."""
        if not text.strip():
            return
        if self.session_ended:
            self.chat_panel.add_system_message("会话已结束，请开始新对话")
            return
        if not self.info_confirmed:
            self.chat_panel.add_system_message("请先填写左侧基本信息并确认")
            return
        threading.Thread(target=self._process_text_pipeline, args=(text,), daemon=True).start()

    # ==================== Pipeline ====================

    def _process_pipeline(self):
        try:
            if self.session_ended:
                self.processing_queue.put(("status", "会话已结束，请开始新对话"))
                return

            # Clear interim report
            self._interim_report = None
            self._interim_pdf_path = None

            # 1. Get audio
            audio_data = self.stt_service.stop_recording()

            if len(audio_data) == 0:
                self.processing_queue.put(("status", "未检测到语音"))
                return

            # 2. Transcribe
            self.processing_queue.put(("status", "正在转写..."))
            text = self.stt_service.transcribe(audio_data)

            if not text.strip():
                self.processing_queue.put(("status", "无法识别内容"))
                return

            self.processing_queue.put(("append_chat", ("user", text)))

            # Increment round
            if self.report_service:
                self.report_service.increment_round()

            # Check time/round warning
            if self.report_service:
                should_warn, warning_msg = self.report_service.should_warn_time_limit()
                if should_warn:
                    self.processing_queue.put(("session_warning", warning_msg))

            # Save user data
            user_id = self.current_user_id or "default_user"
            if self.data_manager:
                self.data_manager.set_user_id(user_id)
                self.data_manager.save_user_message(audio_data, text)

            # 3. LLM generation
            self.processing_queue.put(("status", "正在思考..."))

            system_suffix = ""
            current_rounds = self.report_service.get_round_count() if self.report_service else 0
            allow_relaxation = (current_rounds >= MIN_ROUNDS_FOR_RELAXATION)

            if not allow_relaxation:
                system_suffix = f"【系统警告】当前仅第{current_rounds}轮对话（少于{MIN_ROUNDS_FOR_RELAXATION}轮）。无论用户说了什么，你绝对禁止推荐放松训练！继续通过对话建立关系。"

            # RAG context
            if self.rag_service:
                rag_suffix = self.rag_service.get_system_suffix(text)
                if rag_suffix:
                    system_suffix += "\n" + rag_suffix

            final_suffix = system_suffix if system_suffix.strip() else None

            # Streaming
            full_response = ""
            analysis_text = ""
            spoken_text = ""
            found_separator = False

            self.processing_queue.put(("start_ai_message", None))

            llm_gen = self.llm_service.chat(text, system_suffix=final_suffix)

            stream_buffer = ""
            for chunk in llm_gen:
                full_response += chunk
                stream_buffer += chunk

                # Filter tags
                stream_buffer = re.sub(r'\[REC_[A-Z_]+\]', '', stream_buffer)
                stream_buffer = re.sub(r'\[END_[A-Z_]+\]', '', stream_buffer)
                stream_buffer = re.sub(r'【.*?】', '', stream_buffer)

                # Split on |||
                if not found_separator and '|||' in stream_buffer:
                    parts = stream_buffer.split('|||', 1)
                    analysis_text = parts[0]
                    stream_buffer = parts[1]
                    found_separator = True

                if found_separator and stream_buffer:
                    # Filter emotion tags from display
                    display_text = re.sub(r'<\|[^|]+\|>', '', stream_buffer)
                    if display_text:
                        self.processing_queue.put(("stream_text", display_text))
                    stream_buffer = ""

            self.processing_queue.put(("finish_streaming", None))

            # Parse full response
            if '|||' in full_response:
                parts = full_response.split('|||', 1)
                analysis_text = parts[0].strip()
                spoken_text = parts[1].strip()
            else:
                spoken_text = full_response.strip()

            # Clean spoken text of tags for display
            clean_spoken = re.sub(r'\[REC_[A-Z_]+\]', '', spoken_text)
            clean_spoken = re.sub(r'\[END_[A-Z_]+\]', '', clean_spoken)
            clean_spoken = re.sub(r'<\|[^|]+\|>', '', clean_spoken)
            clean_spoken = re.sub(r'【.*?】', '', clean_spoken).strip()

            self.processing_queue.put(("clean_last_ai", clean_spoken))

            # Check for session end tags
            end_patterns = {
                r'\[END_GOAL_ACHIEVED\]': 'goal_achieved',
                r'\[END_TIME_LIMIT\]': 'time_limit',
                r'\[END_SAFETY\]': 'safety',
                r'\[END_INVALID\]': 'invalid',
                r'\[END_QUIT\]': 'quit',
            }

            end_type = None
            for pattern, etype in end_patterns.items():
                if re.search(pattern, full_response):
                    end_type = etype
                    break

            # Check for relaxation recommendation tags
            rec_tags = {
                r'\[REC_BREATHING\]': 'breathing',
                r'\[REC_MUSCLE\]': 'muscle',
                r'\[REC_MEDITATION\]': 'meditation',
                r'\[REC_GAME\]': 'game',
            }

            relaxation_rec = None
            for pattern, rtype in rec_tags.items():
                if re.search(pattern, full_response):
                    relaxation_rec = rtype
                    break

            # Save assistant message
            if self.data_manager:
                self.data_manager.save_assistant_message(None, full_response, sample_rate=24000)

            # 4. TTS
            self.processing_queue.put(("status", "正在播放..."))
            if self.tts_service and clean_spoken:
                try:
                    self.tts_service.generate_and_play(clean_spoken)
                except Exception as e:
                    print(f"TTS error: {e}")

            # 5. Post-processing
            if end_type:
                if end_type == 'safety':
                    self.processing_queue.put(("show_crisis", None))
                self.processing_queue.put(("session_end", (end_type, "", relaxation_rec, None, True)))
            elif relaxation_rec:
                self.processing_queue.put(("highlight_relax", relaxation_rec))
                self.processing_queue.put(("status", "准备就绪"))
            else:
                self.processing_queue.put(("status", "准备就绪"))

        except Exception as e:
            traceback.print_exc()
            self.processing_queue.put(("error", f"处理出错: {str(e)}"))

    def _process_text_pipeline(self, text):
        """Process text input (no STT, no TTS)."""
        try:
            if self.session_ended:
                self.processing_queue.put(("status", "会话已结束，请开始新对话"))
                return

            self._interim_report = None
            self._interim_pdf_path = None

            self.processing_queue.put(("append_chat", ("user", text)))
            self.processing_queue.put(("set_buttons_state", "disabled"))

            if self.report_service:
                self.report_service.increment_round()

            if self.report_service:
                should_warn, warning_msg = self.report_service.should_warn_time_limit()
                if should_warn:
                    self.processing_queue.put(("session_warning", warning_msg))

            user_id = self.current_user_id or "default_user"
            if self.data_manager:
                self.data_manager.set_user_id(user_id)
                self.data_manager.save_user_message(None, text)

            # LLM generation
            self.processing_queue.put(("status", "正在思考..."))

            system_suffix = ""
            current_rounds = self.report_service.get_round_count() if self.report_service else 0
            allow_relaxation = (current_rounds >= MIN_ROUNDS_FOR_RELAXATION)
            if not allow_relaxation:
                system_suffix = f"【系统警告】当前仅第{current_rounds}轮对话（少于{MIN_ROUNDS_FOR_RELAXATION}轮）。无论用户说了什么，你绝对禁止推荐放松训练！继续通过对话建立关系。"

            if self.rag_service:
                rag_suffix = self.rag_service.get_system_suffix(text)
                if rag_suffix:
                    system_suffix += "\n" + rag_suffix

            final_suffix = system_suffix if system_suffix.strip() else None

            full_response = ""
            analysis_text = ""
            spoken_text = ""
            found_separator = False

            self.processing_queue.put(("start_ai_message", None))

            llm_gen = self.llm_service.chat(text, system_suffix=final_suffix)

            stream_buffer = ""
            for chunk in llm_gen:
                full_response += chunk
                stream_buffer += chunk
                stream_buffer = re.sub(r'\[REC_[A-Z_]+\]', '', stream_buffer)
                stream_buffer = re.sub(r'\[END_[A-Z_]+\]', '', stream_buffer)
                stream_buffer = re.sub(r'【.*?】', '', stream_buffer)

                if not found_separator and '|||' in stream_buffer:
                    parts = stream_buffer.split('|||', 1)
                    analysis_text = parts[0]
                    stream_buffer = parts[1]
                    found_separator = True

                if found_separator and stream_buffer:
                    display_text = re.sub(r'<\|[^|]+\|>', '', stream_buffer)
                    if display_text:
                        self.processing_queue.put(("stream_text", display_text))
                    stream_buffer = ""

            self.processing_queue.put(("finish_streaming", None))

            if '|||' in full_response:
                parts = full_response.split('|||', 1)
                analysis_text = parts[0].strip()
                spoken_text = parts[1].strip()
            else:
                spoken_text = full_response.strip()

            clean_spoken = re.sub(r'\[REC_[A-Z_]+\]', '', spoken_text)
            clean_spoken = re.sub(r'\[END_[A-Z_]+\]', '', clean_spoken)
            clean_spoken = re.sub(r'<\|[^|]+\|>', '', clean_spoken)
            clean_spoken = re.sub(r'【.*?】', '', clean_spoken).strip()

            self.processing_queue.put(("clean_last_ai", clean_spoken))

            end_patterns = {
                r'\[END_GOAL_ACHIEVED\]': 'goal_achieved',
                r'\[END_TIME_LIMIT\]': 'time_limit',
                r'\[END_SAFETY\]': 'safety',
                r'\[END_INVALID\]': 'invalid',
                r'\[END_QUIT\]': 'quit',
            }
            end_type = None
            for pattern, etype in end_patterns.items():
                if re.search(pattern, full_response):
                    end_type = etype
                    break

            rec_tags = {
                r'\[REC_BREATHING\]': 'breathing',
                r'\[REC_MUSCLE\]': 'muscle',
                r'\[REC_MEDITATION\]': 'meditation',
                r'\[REC_GAME\]': 'game',
            }
            relaxation_rec = None
            for pattern, rtype in rec_tags.items():
                if re.search(pattern, full_response):
                    relaxation_rec = rtype
                    break

            if self.data_manager:
                self.data_manager.save_assistant_message(None, full_response, sample_rate=24000)

            if end_type:
                if end_type == 'safety':
                    self.processing_queue.put(("show_crisis", None))
                self.processing_queue.put(("session_end", (end_type, "", relaxation_rec, None, True)))
            elif relaxation_rec:
                self.processing_queue.put(("highlight_relax", relaxation_rec))
                self.processing_queue.put(("status", "准备就绪"))
            else:
                self.processing_queue.put(("status", "准备就绪"))

            self.processing_queue.put(("set_buttons_state", "normal"))

        except Exception as e:
            traceback.print_exc()
            self.processing_queue.put(("error", f"处理出错: {str(e)}"))
            self.processing_queue.put(("set_buttons_state", "normal"))

    # ==================== Queue Processing ====================

    def process_queue(self):
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
                    elif role == "ai_start":
                        self.chat_panel.start_ai_message()

                elif msg_type == "start_ai_message":
                    self.chat_panel.start_ai_message()

                elif msg_type == "stream_text":
                    self.chat_panel.stream_text(content)

                elif msg_type == "finish_streaming":
                    self.chat_panel.finish_streaming()

                elif msg_type == "clean_last_ai":
                    # Replace last AI message with cleaned text
                    pass  # Already cleaned during streaming

                elif msg_type == "session_end":
                    end_type, feedback, relaxation_rec, audio_data = content[:4]
                    play_audio = content[4] if len(content) > 4 else True
                    self._show_session_end_dialog(end_type, feedback, relaxation_rec, audio_data, play_audio)

                elif msg_type == "show_crisis":
                    self._show_crisis_dialog()

                elif msg_type == "highlight_relax":
                    # Map Chinese tags to English keys for control_panel
                    relax_map = {
                        "呼吸": "breathing", "肌肉": "muscle", "冥想": "meditation", "游戏": "game",
                        "breathing": "breathing", "muscle": "muscle", "meditation": "meditation", "game": "game",
                    }
                    relax_key = relax_map.get(content, content)
                    self.control_panel.highlight_relax_button(relax_key)

                elif msg_type == "session_warning":
                    self.chat_panel.add_system_message(content)

                elif msg_type == "enable_ui":
                    self._transition_to_main()

                elif msg_type == "play_greeting":
                    QTimer.singleShot(1000, self._play_opening_greeting)

                elif msg_type == "fill_info_prompt":
                    QTimer.singleShot(1000, self._play_fill_info_prompt)

                elif msg_type == "post_relaxation_greeting":
                    QTimer.singleShot(500, self._play_post_relaxation_greeting)

                elif msg_type == "start_keepalive":
                    self._start_ollama_keepalive()

                elif msg_type == "show_continue_dialog":
                    QTimer.singleShot(100, self._show_continue_or_end_dialog)

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

            self.tts_service = TTSService()

            # STT: load with graceful fallback
            stt_ok = False
            try:
                from config import FUNASR_MODEL_PATH
                if FUNASR_MODEL_PATH and os.path.isdir(FUNASR_MODEL_PATH):
                    self.stt_service = STTService()
                    stt_loaded = threading.Event()
                    tts_loaded = threading.Event()

                    def load_stt():
                        self.stt_service.load_model()
                        stt_loaded.set()

                    def load_tts():
                        self.tts_service.load_model(use_streaming=True)
                        tts_loaded.set()

                    t_stt = threading.Thread(target=load_stt, daemon=True)
                    t_tts = threading.Thread(target=load_tts, daemon=True)
                    t_stt.start()
                    t_tts.start()
                    t_stt.join()
                    t_tts.join()
                    stt_ok = True
                else:
                    self.processing_queue.put(("status", "语音识别模型未找到，跳过 STT"))
                    self.tts_service.load_model(use_streaming=True)
            except Exception as e:
                print(f"[WARNING] STT load failed: {e}")
                self.processing_queue.put(("status", f"语音识别加载失败: {e}"))
                self.stt_service = None
                if not hasattr(self, '_tts_loaded_in_thread'):
                    self.tts_service.load_model(use_streaming=True)

            self.processing_queue.put(("loading_progress", 40))

            if stt_ok and self.stt_service:
                self.processing_queue.put(("status", "正在预热语音识别..."))
                self.stt_service.warmup()
            self.processing_queue.put(("loading_progress", 50))

            self.processing_queue.put(("status", "正在预热语音合成..."))
            self.tts_service.warmup()
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

            # Step 3: Data + Report + RAG
            self.processing_queue.put(("loading_step", "步骤 3/3: 初始化数据"))
            self.processing_queue.put(("status", "正在初始化数据管理..."))
            self.data_manager = DataManager()
            self.data_manager.start_new_session()

            self.report_service = ReportService(self.llm_service)
            self.report_service.start_session()
            self.session_ended = False

            try:
                self.rag_service = get_rag_service()
            except Exception as e:
                print(f"[WARNING] RAG service init failed: {e}")
                self.rag_service = None

            self.processing_queue.put(("loading_progress", 100))
            self.processing_queue.put(("loading_step", "加载完成"))
            self.processing_queue.put(("status", "准备就绪"))

            time.sleep(0.3)
            self.models_loaded = True
            self.processing_queue.put(("enable_ui", None))
            self.processing_queue.put(("fill_info_prompt", None))
            self.processing_queue.put(("start_keepalive", None))

        except Exception as e:
            traceback.print_exc()
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
        if self.llm_service:
            self.llm_service.reset_conversation()
        if self.data_manager:
            self.data_manager.start_new_session()
        if self.report_service:
            self.report_service.start_session()
        self.session_ended = False

    def _show_session_end_dialog(self, end_type, feedback, relaxation_rec, audio_data, play_audio=True):
        report_path = getattr(self, '_interim_pdf_path', None)
        dialog = SessionEndDialog(
            self, end_type, feedback, relaxation_rec,
            report_path=report_path, play_audio=play_audio
        )
        if dialog.exec():
            self._start_new_session()
            self._play_opening_greeting()

    def _show_crisis_dialog(self):
        dialog = CrisisDialog(self, CRISIS_HOTLINES)
        dialog.exec()

    def _show_continue_or_end_dialog(self):
        dialog = ContinueOrEndDialog(self)
        dialog.continue_chosen.connect(self._on_continue_chosen)
        dialog.end_chosen.connect(self._on_end_chosen)
        dialog.exec()

    def _on_continue_chosen(self):
        self.chat_panel.add_system_message("会话继续")
        if self.llm_service:
            self.processing_queue.put(("post_relaxation_greeting", None))

    def _on_end_chosen(self):
        if self.report_service:
            self.report_service.end_session("goal_achieved")

    # ==================== Video / Game ====================

    def _play_video(self, filename):
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        video_path = os.path.join(app_dir, filename)
        if os.path.exists(video_path):
            import subprocess
            if sys.platform == 'win32':
                os.startfile(video_path)
            else:
                subprocess.Popen(['xdg-open', video_path])

            # After video ends, show continue dialog
            QTimer.singleShot(2000, lambda: self.processing_queue.put(("show_continue_dialog", None)))

    def _play_game(self):
        try:
            from services.game_service import get_game_service
            game = get_game_service()
            game.launch()
        except Exception as e:
            print(f"Game error: {e}")

    # ==================== Audio Playback ====================

    def _play_opening_greeting(self):
        """Play the opening greeting."""
        import random
        if self.tts_service:
            greeting = random.choice(GREETING_VARIANTS) if GREETING_VARIANTS else GREETING_MESSAGE
            self.chat_panel.add_system_message(greeting)
            try:
                threading.Thread(
                    target=self.tts_service.generate_and_play,
                    args=(greeting,), daemon=True
                ).start()
            except Exception as e:
                print(f"Greeting TTS error: {e}")

    def _play_post_relaxation_greeting(self):
        if self.tts_service and POST_RELAXATION_MESSAGE:
            self.chat_panel.add_system_message(POST_RELAXATION_MESSAGE)
            try:
                threading.Thread(
                    target=self.tts_service.generate_and_play,
                    args=(POST_RELAXATION_MESSAGE,), daemon=True
                ).start()
            except Exception as e:
                print(f"Post-relaxation TTS error: {e}")

    def _play_fill_info_prompt(self):
        if self.tts_service and FILL_INFO_PROMPT:
            try:
                threading.Thread(
                    target=self.tts_service.generate_and_play,
                    args=(FILL_INFO_PROMPT,), daemon=True
                ).start()
            except Exception as e:
                print(f"Fill info prompt TTS error: {e}")

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
        if self.tts_service:
            self.tts_service.cleanup()
        QApplication.quit()

    def closeEvent(self, event):
        if self.tts_service:
            self.tts_service.cleanup()
        event.accept()

    # ==================== Missing Business Logic ====================

    def _save_analysis_log(self, user_id, user_text, analysis, spoken):
        """Save psychological analysis to log file."""
        try:
            from datetime import datetime
            if self.data_manager and self.data_manager.current_folder_name:
                session_path = self.data_manager._get_session_path()
                log_path = session_path / "analysis_log.txt"
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(f"\n{'='*60}\n")
                    f.write(f"[{timestamp}] 用户: {user_text}\n")
                    f.write(f"\n--- 心理分析 ---\n{analysis}\n")
                    f.write(f"\n--- 口语回复 ---\n{spoken}\n")
        except Exception as e:
            print(f"[WARNING] 保存分析日志失败: {e}")

    def _clean_text_for_ui(self, text):
        """Remove control tags and TTS emotion tags for UI display."""
        if not text:
            return ""
        text = re.sub(r'【.*?】', '', text)
        text = re.sub(r'\[REC_[A-Z_]+\]', '', text)
        text = re.sub(r'\s*<\|[^>]+\|>\s*', '', text)
        text = re.sub(r'\[END_[A-Z_]+\]', '', text)
        return text.strip()

    def _get_relaxation_info_str(self):
        """Get Chinese description of relaxation training."""
        relax_map = {
            "huxi": "呼吸放松训练", "jirou": "渐进式肌肉放松",
            "mingxiang": "冥想正念训练", "冥想训练": "冥想正念训练",
            "肌肉放松训练": "渐进式肌肉放松", "肌肉放松": "渐进式肌肉放松",
            "呼吸放松训练": "呼吸放松训练", "呼吸训练": "呼吸放松训练",
            "unknown": "未知"
        }
        raw_type = getattr(self, '_current_relaxation_type', "")
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
                    print(f"[INFO] PDF报告已生成: {pdf_path}")
                    return pdf_path
        except Exception as e:
            print(f"[WARNING] PDF生成失败: {e}")
            traceback.print_exc()
        return None

    def _infer_relaxation_tag(self, spoken_text, allow_relaxation, current_rounds):
        """Infer relaxation recommendation tag from spoken text."""
        if not allow_relaxation:
            return None
        if "呼吸" in spoken_text and ("按钮" in spoken_text or "练习" in spoken_text):
            tag = "[REC_BREATHING]"
        elif "肌肉" in spoken_text and ("按钮" in spoken_text or "练习" in spoken_text):
            tag = "[REC_MUSCLE]"
        elif "冥想" in spoken_text and ("按钮" in spoken_text or "练习" in spoken_text):
            tag = "[REC_MEDITATION]"
        elif "游戏" in spoken_text:
            tag = "[REC_GAME]"
        elif "冥想" in spoken_text:
            tag = "[REC_MEDITATION]"
        elif "呼吸" in spoken_text:
            tag = "[REC_BREATHING]"
        elif "肌肉" in spoken_text:
            tag = "[REC_MUSCLE]"
        else:
            return None
        print(f"[DEBUG] Inferred relaxation tag: {tag}")
        self._last_relaxation_recommendation_round = current_rounds
        return tag

    def _handle_session_end(self, end_type, relaxation_tag=None):
        """Handle session end: generate reports and trigger UI updates."""
        from services.report_service import EndType
        self.session_ended = True

        if not relaxation_tag and not getattr(self, '_current_relaxation_type', None):
            try:
                conversation_history = self.llm_service.conversation_history
                relaxation_tag = self.report_service.recommend_relaxation_strategy(conversation_history)
            except Exception as e:
                print(f"[WARNING] 智能推荐失败: {e}")
                relaxation_tag = "呼吸"

        if end_type not in [EndType.SAFETY, EndType.INVALID] and \
           not getattr(self, '_current_relaxation_type', None) and \
           not getattr(self, '_has_forced_relaxation_rec', False):
            self._has_forced_relaxation_rec = True
            self.session_ended = False
            rec_text = f"等等，在结束之前，我留意到你还是有点紧张。要不咱们先做个{relaxation_tag}放松训练？只需几分钟，效果很好的。"
            self.processing_queue.put(("append_chat", ("ai", rec_text)))
            threading.Thread(target=lambda: self.tts_service.generate_and_play(rec_text), daemon=True).start()
            time.sleep(1)
            self.processing_queue.put(("highlight_relax", relaxation_tag))
            self.processing_queue.put(("status", "请尝试放松训练"))
            return

        self.processing_queue.put(("status", "正在生成反馈..."))

        def generate_reports():
            try:
                user_id = self.current_user_id or "default_user"
                current_user_info = getattr(self, "user_info", {})
                conversation_history = self.llm_service.conversation_history

                relaxation_rec = None
                if relaxation_tag:
                    tag_map = {"呼吸": "BREATHING", "肌肉": "MUSCLE", "冥想": "MEDITATION"}
                    relaxation_rec = tag_map.get(relaxation_tag)

                # Generate visitor feedback
                self.processing_queue.put(("start_ai_message", None))
                full_feedback = ""
                stream_gen = self.report_service.generate_visitor_feedback(
                    conversation_history, end_type, relaxation_rec, stream=True
                )
                for chunk in stream_gen:
                    full_feedback += chunk
                    clean = re.sub(r'<\|[^>]+\|>', '', chunk)
                    clean = re.sub(r'\[REC_[A-Z_]+\]', '', clean)
                    clean = re.sub(r'\[END_[A-Z_]+\]', '', clean)
                    if clean:
                        self.processing_queue.put(("stream_text", clean))
                self.processing_queue.put(("finish_streaming", None))

                # Generate researcher report
                relax_str = self._get_relaxation_info_str()
                researcher_report = self.report_service.generate_researcher_report(
                    conversation_history, user_id, end_type,
                    user_info=current_user_info, relaxation_info=relax_str
                )
                if isinstance(researcher_report, dict):
                    researcher_report["relaxation_completed"] = True
                    researcher_report["relaxation_type"] = relax_str

                # Save report
                save_result = self.data_manager.save_session_report(
                    researcher_report, full_feedback, end_type.value
                )

                # Generate PDF
                pdf_path = self._generate_and_save_pdf(researcher_report, user_id, end_type, save_result)

                self.processing_queue.put(("session_end", (end_type, full_feedback, relaxation_rec, None, False)))
                if end_type == EndType.SAFETY:
                    self.processing_queue.put(("show_crisis", None))

                self.processing_queue.put(("status", "会话已结束 - 可开始新会话"))

            except Exception as e:
                traceback.print_exc()
                self.processing_queue.put(("error", f"报告生成失败: {str(e)}"))

        threading.Thread(target=generate_reports, daemon=True).start()

    def _play_video_with_report(self, filename):
        """Play relaxation video with background report generation."""
        from services.video_service import get_video_player
        from services.report_service import EndType

        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        video_path = os.path.join(app_dir, filename)
        if not os.path.exists(video_path):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "警告", f"文件不存在: {filename}")
            return

        self.control_panel.stop_all_blinks()
        self._current_relaxation_type = filename.replace(".mp4", "")
        self._relaxation_completed_flag = False
        self._has_forced_relaxation_rec = False

        if self.report_service:
            relax_name = filename.replace(".mp4", "")
            self.report_service.record_relaxation(relax_name)

        def video_runner():
            try:
                player = get_video_player()
                player.play_video(video_path)
            except Exception as e:
                print(f"Video error: {e}")
            finally:
                if self.report_service:
                    relax_name = filename.replace(".mp4", "")
                    self.report_service.record_relaxation(relax_name)
                self.processing_queue.put(("post_relaxation_greeting", None))

        threading.Thread(target=video_runner, daemon=True).start()

    def _on_continue_chosen_with_report(self):
        """Continue chat after relaxation with interim report."""
        self.chat_panel.add_system_message("放松训练已完成，可以继续对话。")
        self._relaxation_completed_flag = True
        self.processing_queue.put(("post_relaxation_greeting", None))

    def _on_end_chosen_with_report(self):
        """End session after relaxation."""
        self._handle_session_end(EndType.GOAL_ACHIEVED)

    def _launch_therapeutic_game(self):
        """Launch the therapeutic game."""
        from services.game_service import get_game_service

        user_id = self.control_panel.get_user_info().get("user_id", "default_user")
        session_folder = getattr(self, '_session_folder', None)
        if not session_folder:
            from datetime import datetime
            session_folder = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "sessions", f"{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )
            os.makedirs(session_folder, exist_ok=True)
            self._session_folder = session_folder

        self.control_panel.set_status("正在启动心理互动游戏...")
        self.chat_panel.add_system_message("心理互动游戏启动中...")
        self.control_panel.set_buttons_enabled(False)

        def run_game():
            try:
                game_service = get_game_service()
                results = game_service.play_game(session_folder)
                self._game_results = results
                QTimer.singleShot(0, lambda: self._on_game_complete(results))
            except Exception as e:
                print(f"[ERROR] Game failed: {e}")
                traceback.print_exc()
                QTimer.singleShot(0, lambda: self._on_game_error(str(e)))

        threading.Thread(target=run_game, daemon=True).start()

    def _on_game_complete(self, results):
        self.control_panel.set_buttons_enabled(True)
        accuracy = results.get("go_nogo_accuracy", 0)
        breathing_rate = results.get("breathing_completion_rate", 0)
        camps = results.get("camp_structures_built", 0)
        summary = f"游戏完成！反应准确率: {accuracy}%，呼吸完成率: {breathing_rate}%，建造营地: {camps}个"
        self.control_panel.set_status(summary)
        self.chat_panel.add_system_message(summary)
        if self.report_service:
            self.report_service.record_game_session(results)

    def _on_game_error(self, error_msg):
        self.control_panel.set_buttons_enabled(True)
        self.control_panel.set_status(f"游戏启动失败: {error_msg}")
        self.chat_panel.add_system_message(f"游戏启动失败: {error_msg}")
