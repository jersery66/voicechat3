# Main Window - Application Shell

import os
import sys
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QStackedWidget, QLabel, QFrame, QSplashScreen,
    QApplication, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QPixmap, QPainter, QBrush, QColor, QPalette

# Add parent directory to path for config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import APP_NAME, WINDOW_WIDTH, WINDOW_HEIGHT

from .chat_widget import ChatWidget
from .control_panel import ControlPanel
from .settings_dialog import SettingsDialog


class ModelLoaderThread(QThread):
    """Thread for loading AI models in background."""
    
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)
    
    def __init__(self, stt_service, tts_service):
        super().__init__()
        self.stt_service = stt_service
        self.tts_service = tts_service
        
    def run(self):
        try:
            # Load STT model
            self.progress.emit("正在加载语音识别模型...")
            self.stt_service.load_model(progress_callback=lambda x: self.progress.emit(x))
            
            # Load TTS model  
            self.progress.emit("正在加载语音合成模型...")
            self.tts_service.load_model(progress_callback=lambda x: self.progress.emit(x))
            
            self.finished.emit(True, "模型加载完成!")
        except Exception as e:
            self.finished.emit(False, f"模型加载失败: {str(e)}")


class ConversationThread(QThread):
    """Thread for handling the full conversation pipeline."""
    
    status_update = pyqtSignal(str)
    transcription_ready = pyqtSignal(str)
    llm_chunk = pyqtSignal(str)
    llm_finished = pyqtSignal(str)
    tts_finished = pyqtSignal(object)  # numpy array
    error = pyqtSignal(str)
    
    def __init__(self, stt_service, llm_service, tts_service, audio_data):
        super().__init__()
        self.stt_service = stt_service
        self.llm_service = llm_service
        self.tts_service = tts_service
        self.audio_data = audio_data
        
    def run(self):
        try:
            # Step 1: Transcribe
            self.status_update.emit("📝 正在识别...")
            text = self.stt_service.transcribe(self.audio_data)
            if not text.strip():
                self.error.emit("未检测到语音内容")
                return
            self.transcription_ready.emit(text)
            
            # Step 2: LLM response (streaming)
            self.status_update.emit("🤔 正在思考...")
            full_response = ""
            for chunk in self.llm_service.chat(text):
                full_response += chunk
                self.llm_chunk.emit(chunk)
            self.llm_finished.emit(full_response)
            
            # Step 3: TTS (streaming playback)
            self.status_update.emit("🔊 正在播放...")
            audio = self.tts_service.generate_and_play(full_response)
            self.tts_finished.emit(audio)
            
        except Exception as e:
            self.error.emit(f"处理出错: {str(e)}")


class SidebarButton(QPushButton):
    """Styled sidebar navigation button."""
    
    def __init__(self, text: str, icon: str = "", parent=None):
        super().__init__(parent)
        self.setText(f"{icon} {text}" if icon else text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(QFont("Microsoft YaHei", 11))


class MainWindow(QMainWindow):
    """Main application window."""
    
    def __init__(self):
        super().__init__()
        
        self.settings = {
            "user_id": "default_user",
            "background_image": None,
            "ollama_model": "qwen2.5:7b"
        }
        
        # Services (will be initialized later)
        self.stt_service = None
        self.llm_service = None
        self.tts_service = None
        self.data_manager = None
        
        self.models_loaded = False
        
        self._setup_window()
        self._setup_ui()
        self._load_stylesheet()
        self._connect_signals()
        
    def _setup_window(self):
        """Configure window properties."""
        self.setWindowTitle(APP_NAME)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(900, 600)
        
        # Set background image
        bg_path = Path(__file__).parent / "background.jpg"
        if bg_path.exists():
            self.setStyleSheet(f"""
                QMainWindow {{
                    background-image: url({bg_path.as_posix()});
                    background-position: center;
                    background-repeat: no-repeat;
                    background-attachment: fixed;
                }}
            """)
        
    def _setup_ui(self):
        """Setup the main UI layout."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Sidebar
        sidebar = self._create_sidebar()
        main_layout.addWidget(sidebar)
        
        # Content area
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(16, 16, 16, 0)
        content_layout.setSpacing(0)
        
        # Stacked widget for different views
        self.stack = QStackedWidget()
        
        # Chat view
        self.chat_view = self._create_chat_view()
        self.stack.addWidget(self.chat_view)
        
        # Video view (placeholder)
        self.video_view = self._create_video_view()
        self.stack.addWidget(self.video_view)
        
        content_layout.addWidget(self.stack, 1)
        
        # Control panel
        self.control_panel = ControlPanel()
        content_layout.addWidget(self.control_panel)
        
        main_layout.addLayout(content_layout, 1)
        
    def _create_sidebar(self) -> QFrame:
        """Create the sidebar navigation."""
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(200)
        
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(8)
        
        # App title
        title = QLabel(APP_NAME)
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(20)
        
        # Navigation buttons
        self.chat_btn = SidebarButton("对话", "💬")
        self.chat_btn.setChecked(True)
        self.chat_btn.clicked.connect(lambda: self._switch_view(0))
        layout.addWidget(self.chat_btn)
        
        self.video_btn = SidebarButton("放松训练", "🎬")
        self.video_btn.clicked.connect(lambda: self._switch_view(1))
        layout.addWidget(self.video_btn)
        
        layout.addStretch()
        
        # Settings button
        self.settings_btn = SidebarButton("设置", "⚙️")
        self.settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(self.settings_btn)
        
        return sidebar
        
    def _create_chat_view(self) -> QWidget:
        """Create the chat view."""
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.chat_widget = ChatWidget()
        layout.addWidget(self.chat_widget)
        
        return view
        
    def _create_video_view(self) -> QWidget:
        """Create the relaxation video view with buttons for training videos."""
        view = QWidget()
        view.setObjectName("videoView")
        layout = QVBoxLayout(view)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(30)
        
        # Title
        label = QLabel("🎬 放松训练视频")
        label.setFont(QFont("Microsoft YaHei", 24, QFont.Weight.Bold))
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: #FFFFFF; margin-bottom: 20px;")
        layout.addWidget(label)
        
        # Subtitle
        subtitle = QLabel("请选择您想要进行的放松训练")
        subtitle.setFont(QFont("Microsoft YaHei", 14))
        subtitle.setStyleSheet("color: #9CA3AF; margin-bottom: 30px;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)
        
        # Buttons container
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(30)
        buttons_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Video file paths
        app_dir = Path(__file__).parent.parent
        
        # Muscle Relaxation Button
        muscle_btn = self._create_video_button(
            "💪", "肌肉放松", "渐进式肌肉放松训练",
            app_dir / "肌肉放松.mp4",
            "#EF4444", "#DC2626"
        )
        buttons_layout.addWidget(muscle_btn)
        
        # Breathing Training Button
        breath_btn = self._create_video_button(
            "🌬️", "呼吸训练", "深度呼吸放松练习",
            app_dir / "呼吸训练.mp4",
            "#22C55E", "#16A34A"
        )
        buttons_layout.addWidget(breath_btn)
        
        # Meditation Training Button
        meditation_btn = self._create_video_button(
            "🧘", "冥想训练", "引导式冥想放松",
            app_dir / "冥想训练.mp4",
            "#8B5CF6", "#7C3AED"
        )
        buttons_layout.addWidget(meditation_btn)
        
        layout.addLayout(buttons_layout)
        layout.addStretch()
        
        return view
        
    def _create_video_button(self, icon: str, title: str, desc: str, video_path: Path, color1: str, color2: str) -> QWidget:
        """Create a styled video button card."""
        card = QFrame()
        card.setFixedSize(200, 220)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setStyleSheet(f"""
            QFrame {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                                            stop:0 {color1}, stop:1 {color2});
                border-radius: 20px;
                padding: 20px;
            }}
            QFrame:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                                            stop:0 {color2}, stop:1 {color1});
            }}
        """)
        
        layout = QVBoxLayout(card)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)
        
        # Icon
        icon_label = QLabel(icon)
        icon_label.setFont(QFont("Segoe UI Emoji", 48))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet("background: transparent;")
        layout.addWidget(icon_label)
        
        # Title
        title_label = QLabel(title)
        title_label.setFont(QFont("Microsoft YaHei", 16, QFont.Weight.Bold))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("color: #FFFFFF; background: transparent;")
        layout.addWidget(title_label)
        
        # Description
        desc_label = QLabel(desc)
        desc_label.setFont(QFont("Microsoft YaHei", 10))
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_label.setStyleSheet("color: rgba(255, 255, 255, 0.8); background: transparent;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)
        
        # Add shadow effect
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(0, 0, 0, 80))
        card.setGraphicsEffect(shadow)
        
        # Make the card clickable
        card.mousePressEvent = lambda e: self._play_video_fullscreen(video_path)
        
        return card
        
    def _play_video_fullscreen(self, video_path: Path):
        """Play the video in fullscreen using the default system player."""
        if video_path.exists():
            # Use Windows Media Player in fullscreen mode or default player
            if sys.platform == 'win32':
                # Try to open with default associated application
                os.startfile(str(video_path))
            else:
                subprocess.Popen(['xdg-open', str(video_path)])
        
    def _switch_view(self, index: int):
        """Switch between views."""
        self.stack.setCurrentIndex(index)
        
        # Update button states
        self.chat_btn.setChecked(index == 0)
        self.video_btn.setChecked(index == 1)
        
    def _load_stylesheet(self):
        """Load the QSS stylesheet."""
        qss_path = Path(__file__).parent / "styles.qss"
        if qss_path.exists():
            with open(qss_path, 'r', encoding='utf-8') as f:
                self.setStyleSheet(f.read())
                
    def _connect_signals(self):
        """Connect UI signals."""
        self.control_panel.recording_started.connect(self._on_recording_started)
        self.control_panel.recording_stopped.connect(self._on_recording_stopped)
        self.chat_widget.clear_button.clicked.connect(self._on_clear_chat)
        
    def _open_settings(self):
        """Open settings dialog."""
        dialog = SettingsDialog(self, self.settings)
        dialog.settings_changed.connect(self._apply_settings)
        dialog.exec()
        
    def _apply_settings(self, new_settings: dict):
        """Apply new settings."""
        self.settings = new_settings
        
        # Update user ID in data manager
        if self.data_manager:
            self.data_manager.set_user_id(new_settings.get("user_id", "default_user"))
            
        # Update LLM model
        if self.llm_service:
            self.llm_service.model = new_settings.get("ollama_model", "qwen2.5:7b")
            
        # Update background
        bg_path = new_settings.get("background_image")
        self._set_background(bg_path)
        
    def _set_background(self, image_path: str = None):
        """Set the chat background image."""
        if image_path and os.path.exists(image_path):
            self.chat_view.setStyleSheet(f"""
                background-image: url({image_path});
                background-position: center;
                background-repeat: no-repeat;
            """)
        else:
            self.chat_view.setStyleSheet("")
            
    def initialize_services(self):
        """Initialize AI services."""
        from services.stt_service import get_stt_service
        from services.llm_service import get_llm_service
        from services.tts_service import get_tts_service
        from data.data_manager import get_data_manager
        
        self.stt_service = get_stt_service()
        self.llm_service = get_llm_service()
        self.tts_service = get_tts_service()
        self.data_manager = get_data_manager()
        
        # Start loading models
        self.control_panel.set_enabled(False)
        self.control_panel.set_status("正在加载模型...", True)
        
        self.loader_thread = ModelLoaderThread(self.stt_service, self.tts_service)
        self.loader_thread.progress.connect(lambda msg: self.control_panel.set_status(msg, True))
        self.loader_thread.finished.connect(self._on_models_loaded)
        self.loader_thread.start()
        
    def _on_models_loaded(self, success: bool, message: str):
        """Handle model loading completion."""
        self.models_loaded = success
        self.control_panel.set_status(message, False)
        
        if success:
            self.control_panel.set_enabled(True)
            self.control_panel.reset()
            # Start a new session
            self.data_manager.start_new_session()
        else:
            self.control_panel.set_status(f"❌ {message}", False)
            
    def _on_recording_started(self):
        """Handle recording start."""
        if self.stt_service:
            self.stt_service.start_recording()
            
    def _on_recording_stopped(self):
        """Handle recording stop and start processing."""
        if not self.stt_service:
            return
            
        # Get recorded audio
        audio = self.stt_service.stop_recording()
        
        if len(audio) < 1600:  # Less than 0.1s
            self.control_panel.set_status("录音太短，请重试", False)
            QTimer.singleShot(2000, self.control_panel.reset)
            return
            
        # Start conversation thread
        self.conv_thread = ConversationThread(
            self.stt_service, 
            self.llm_service, 
            self.tts_service,
            audio
        )
        self.conv_thread.status_update.connect(lambda msg: self.control_panel.set_status(msg, True))
        self.conv_thread.transcription_ready.connect(self._on_transcription)
        self.conv_thread.llm_chunk.connect(self._on_llm_chunk)
        self.conv_thread.llm_finished.connect(self._on_llm_finished)
        self.conv_thread.tts_finished.connect(self._on_tts_finished)
        self.conv_thread.error.connect(self._on_error)
        self.conv_thread.start()
        
        # Store audio for saving later
        self._current_user_audio = audio
        
    def _on_transcription(self, text: str):
        """Handle transcription result."""
        self.chat_widget.add_user_message(text)
        self._current_transcription = text
        
        # Save user message
        if self.data_manager:
            self.data_manager.save_user_message(self._current_user_audio, text)
            
        # Prepare for assistant response
        self.chat_widget.add_assistant_message("")
        
    def _on_llm_chunk(self, chunk: str):
        """Handle LLM response chunk."""
        self.chat_widget.stream_text(chunk)
        
    def _on_llm_finished(self, full_response: str):
        """Handle LLM response completion."""
        self.chat_widget.finish_streaming()
        self._current_response = full_response
        
    def _on_tts_finished(self, audio):
        """Handle TTS completion."""
        # Save assistant message
        if self.data_manager and audio is not None and len(audio) > 0:
            self.data_manager.save_assistant_message(audio, self._current_response, sample_rate=24000)
            
        self.control_panel.reset()
        
    def _on_error(self, error_msg: str):
        """Handle error."""
        self.control_panel.set_status(f"❌ {error_msg}", False)
        QTimer.singleShot(3000, self.control_panel.reset)
        
    def _on_clear_chat(self):
        """Handle clear chat button."""
        self.chat_widget.clear_chat()
        
        # Reset LLM conversation history
        if self.llm_service:
            self.llm_service.reset_conversation()
            
        # Start a new session
        if self.data_manager:
            self.data_manager.start_new_session()
            
    def closeEvent(self, event):
        """Handle window close."""
        # Cleanup
        if self.tts_service:
            self.tts_service.cleanup()
        event.accept()
