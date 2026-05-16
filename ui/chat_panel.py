# Chat Panel - Right side panel with scrollable message area

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QSizePolicy, QTextEdit
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont, QKeyEvent

from .widgets import FrostedPanel, MessageBubble, StatusIndicator


class ChatInput(QTextEdit):
    """Text input that sends on Enter, newline on Shift+Enter."""
    submit = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("输入消息，按 Enter 发送...")
        self.setFixedHeight(60)
        self.setStyleSheet("""
            QTextEdit {
                background: rgba(255,255,255,0.85);
                border: 1px solid #c4a96a;
                border-radius: 8px;
                padding: 6px 10px;
                font-size: 12px;
                color: #2c3e50;
            }
            QTextEdit:focus { border: 1px solid #8b7355; }
        """)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
            self.submit.emit()
            return
        super().keyPressEvent(event)


class ChatPanel(FrostedPanel):
    """Right chat panel with message bubbles and streaming support."""

    clear_clicked = Signal()
    exit_clicked = Signal()
    text_submitted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(alpha=0.60, radius=16, parent=None)
        self.setObjectName("chatPanel")
        self.setFixedWidth(380)
        self.setMaximumHeight(600)
        self._messages = []
        self._current_streaming_bubble = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)

        # Header
        header = QHBoxLayout()
        title = QLabel("对话记录")
        title.setObjectName("chatTitle")
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        title.setStyleSheet("color: #2d5a27;")
        header.addWidget(title)

        header.addStretch()

        self.btn_clear_header = QPushButton("清空")
        self.btn_clear_header.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #d0d0d0;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11px;
                color: #7f8c8d;
            }
            QPushButton:hover {
                background-color: #f0f0f0;
                border: 1px solid #b0b0b0;
            }
        """)
        self.btn_clear_header.setCursor(Qt.PointingHandCursor)
        self.btn_clear_header.clicked.connect(self._on_clear)
        header.addWidget(self.btn_clear_header)

        layout.addLayout(header)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #e0e0e0;")
        layout.addWidget(sep)

        # Scroll area for messages
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("chatScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(0, 0, 0, 0.12);
                border-radius: 3px;
                min-height: 30px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)

        # Messages container
        self._msg_container = QWidget()
        self._msg_container.setStyleSheet("background: transparent;")
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(6, 8, 6, 8)
        self._msg_layout.setSpacing(8)
        self._msg_layout.addStretch()

        self.scroll_area.setWidget(self._msg_container)
        layout.addWidget(self.scroll_area)

        # AI status indicator
        self.status_indicator = StatusIndicator()
        layout.addWidget(self.status_indicator)

        # Text input area
        input_layout = QHBoxLayout()
        input_layout.setSpacing(6)

        self.text_input = ChatInput()
        self.text_input.submit.connect(self._on_send)
        input_layout.addWidget(self.text_input)

        self.btn_send = QPushButton("发送")
        self.btn_send.setFixedSize(50, 60)
        self.btn_send.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50; color: white;
                border: 1px solid #388E3C; border-radius: 8px;
                font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #388E3C; }
        """)
        self.btn_send.setCursor(Qt.PointingHandCursor)
        self.btn_send.clicked.connect(self._on_send)
        input_layout.addWidget(self.btn_send)

        layout.addLayout(input_layout)

        # Bottom buttons (clear + exit)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self.btn_clear = QPushButton("清除记录")
        self.btn_clear.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #d0d0d0;
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 11px;
                color: #7f8c8d;
            }
            QPushButton:hover { background-color: #f0f0f0; border: 1px solid #b0b0b0; }
        """)
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(self.clear_clicked.emit)
        btn_layout.addWidget(self.btn_clear)

        self.btn_exit = QPushButton("退出")
        self.btn_exit.setStyleSheet("""
            QPushButton {
                background-color: #EF5350; color: white;
                border: 1px solid #C62828; border-radius: 6px;
                padding: 5px 10px; font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background-color: #C62828; }
        """)
        self.btn_exit.setCursor(Qt.PointingHandCursor)
        self.btn_exit.clicked.connect(self.exit_clicked.emit)
        btn_layout.addWidget(self.btn_exit)

        layout.addLayout(btn_layout)

    def add_user_message(self, text):
        """Add a user message bubble."""
        bubble = MessageBubble(text, is_user=True)
        self._add_bubble(bubble)
        self._messages.append({"type": "user", "text": text, "bubble": bubble})

    def add_system_message(self, text):
        """Add a system message (centered, gray)."""
        bubble = MessageBubble(text, is_system=True)
        self._add_bubble(bubble)
        self._messages.append({"type": "system", "text": text, "bubble": bubble})

    def start_ai_message(self):
        """Start a new AI message bubble for streaming."""
        bubble = MessageBubble("", is_user=False)
        self._add_bubble(bubble)
        self._messages.append({"type": "ai", "text": "", "bubble": bubble})
        self._current_streaming_bubble = bubble
        self.status_indicator.set_state("generating")

    def stream_text(self, chunk):
        """Append text chunk to current streaming message."""
        if self._current_streaming_bubble:
            self._current_streaming_bubble.append_text(chunk)
            if self._messages:
                self._messages[-1]["text"] = self._current_streaming_bubble.full_text
            self._scroll_to_bottom()

    def finish_streaming(self):
        """Mark streaming as complete."""
        self._current_streaming_bubble = None
        self.status_indicator.set_state("idle")

    def set_ai_status(self, status):
        """External control: 'idle', 'thinking', 'generating', 'speaking'."""
        self.status_indicator.set_state(status)

    def _add_bubble(self, bubble):
        """Insert bubble before the stretch."""
        count = self._msg_layout.count()
        self._msg_layout.insertWidget(count - 1, bubble)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        QTimer.singleShot(30, lambda: self.scroll_area.verticalScrollBar().setValue(
            self.scroll_area.verticalScrollBar().maximum()
        ))

    def _on_send(self):
        text = self.text_input.toPlainText().strip()
        if text:
            self.text_submitted.emit(text)
            self.text_input.clear()

    def set_input_enabled(self, enabled):
        self.text_input.setEnabled(enabled)
        self.btn_send.setEnabled(enabled)

    def _on_clear(self):
        self.clear_chat()
        self.clear_clicked.emit()

    def clear_chat(self):
        """Remove all message bubbles."""
        for msg in self._messages:
            bubble = msg.get("bubble")
            if bubble:
                bubble.deleteLater()
        self._messages.clear()
        self._current_streaming_bubble = None
