# Chat Panel - Right side panel with scrollable message area

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QSizePolicy, QTextEdit, QApplication
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
    end_session_clicked = Signal()
    text_submitted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(alpha=0.94, radius=20, parent=None)
        self.setObjectName("chatPanel")
        self.setMinimumWidth(400)
        self.setMaximumWidth(760)
        self._messages = []
        self._current_streaming_bubble = None
        self._active_generation_id = None
        self._next_generation_seq = 0
        # Typewriter reveal: text appears at roughly speaking pace instead of
        # as instant token bursts, so the bubble keeps up with the voice.
        self._type_buffer = ""
        self._type_bubble = None
        self._type_finished = False
        self._setup_ui()
        self._type_ticker = QTimer(self)
        self._type_ticker.timeout.connect(self._reveal_tick)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)

        # Header: title block (name + role) on the left, quiet action on the right
        header = QHBoxLayout()
        title_block = QVBoxLayout()
        title_block.setContentsMargins(2, 0, 0, 0)
        title_block.setSpacing(0)

        title = QLabel("对话记录")
        title.setObjectName("chatTitle")
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        title.setStyleSheet("color: #1E2A32;")
        title_block.addWidget(title)

        subtitle = QLabel("小薇 · 心理支持访谈")
        subtitle.setStyleSheet("color: #7C8A82; font-size: 11px; padding: 0 0 4px 9px;")
        title_block.addWidget(subtitle)
        header.addLayout(title_block)

        header.addStretch()

        self.btn_clear_header = QPushButton("清空")
        self.btn_clear_header.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid rgba(30, 42, 50, 0.14);
                border-radius: 8px;
                padding: 5px 12px;
                font-size: 11px;
                color: #5C6B73;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.9);
                border: 1px solid rgba(47, 107, 79, 0.30);
                color: #2F6B4F;
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
                width: 8px;
                margin: 4px 2px 4px 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(30, 42, 50, 0.16);
                border-radius: 3px;
                min-height: 32px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(30, 42, 50, 0.28);
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

        # Top spacer: messages stay at bottom when few, auto-compress when many
        from PySide6.QtWidgets import QSpacerItem, QSizePolicy
        self._top_spacer = QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding)
        self._msg_layout.addItem(self._top_spacer)

        self.scroll_area.setWidget(self._msg_container)

        # Auto-pin to bottom when content height changes (like WeChat)
        self.scroll_area.verticalScrollBar().rangeChanged.connect(
            self._on_scroll_range_changed
        )

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
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2F6B4F, stop:1 #3C8163);
                color: #FFFFFF; border: none; border-radius: 12px;
                font-size: 12px; font-weight: 700;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #275C43, stop:1 #337257);
            }
            QPushButton:disabled { background: rgba(47, 107, 79, 0.35); }
        """)
        self.btn_send.setCursor(Qt.PointingHandCursor)
        self.btn_send.clicked.connect(self._on_send)
        input_layout.addWidget(self.btn_send)

        # Opaque composer base: keeps typed text readable and hides the
        # photo's own bottom-right watermark that bled through the glass card.
        footer = QFrame()
        footer.setObjectName("chatFooter")
        footer.setStyleSheet("""
            #chatFooter {
                background-color: rgba(255, 255, 255, 0.97);
                border-top: 1px solid rgba(30, 42, 50, 0.08);
                border-bottom-left-radius: 20px;
                border-bottom-right-radius: 20px;
            }
        """)
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(10, 8, 10, 10)
        footer_layout.setSpacing(6)
        footer_layout.addLayout(input_layout)
        layout.addWidget(footer)

        # Bottom buttons (clear + exit)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self.btn_clear = QPushButton("清除记录")
        self.btn_clear.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid rgba(30, 42, 50, 0.14);
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 11px;
                color: #5C6B73;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.9); border: 1px solid rgba(47, 107, 79, 0.30); }
        """)
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(self.clear_clicked.emit)
        self.btn_clear.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        btn_layout.addWidget(self.btn_clear)
        btn_layout.addStretch(1)

        self.btn_end_session = QPushButton("结束会话")
        self.btn_end_session.setStyleSheet("""
            QPushButton {
                background-color: rgba(196, 169, 106, 0.18); color: #6B5230;
                border: 1px solid rgba(196, 169, 106, 0.35); border-radius: 8px;
                padding: 6px 12px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: rgba(196, 169, 106, 0.30); }
        """)
        self.btn_end_session.setCursor(Qt.PointingHandCursor)
        self.btn_end_session.clicked.connect(self.end_session_clicked.emit)
        self.btn_end_session.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        btn_layout.addWidget(self.btn_end_session)

        self.btn_exit = QPushButton("退出")
        self.btn_exit.setStyleSheet("""
            QPushButton {
                background-color: rgba(192, 80, 60, 0.10); color: #A94432;
                border: 1px solid rgba(192, 80, 60, 0.28); border-radius: 8px;
                padding: 6px 12px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: rgba(192, 80, 60, 0.20); }
        """)
        self.btn_exit.setCursor(Qt.PointingHandCursor)
        self.btn_exit.clicked.connect(self.exit_clicked.emit)
        self.btn_exit.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        btn_layout.addWidget(self.btn_exit)

        footer_layout.addLayout(btn_layout)

    def add_user_message(self, text):
        """Add a user message bubble."""
        bubble = MessageBubble(text, is_user=True)
        self._add_bubble(bubble)
        self._messages.append({"type": "user", "text": text, "bubble": bubble})

    def add_system_message(self, text, as_ai=False):
        """Add a system message (centered, gray) or as an AI-style message."""
        bubble = MessageBubble(text, is_user=False, is_system=not as_ai)
        self._add_bubble(bubble)
        msg_type = "ai" if as_ai else "system"
        self._messages.append({"type": msg_type, "text": text, "bubble": bubble})

    def replace_last_ai_message(self, text):
        """Replace the text of the last AI message bubble (for dynamic greeting updates)."""
        self._flush_typing()
        for msg in reversed(self._messages):
            if msg["type"] == "ai":
                msg["bubble"].set_text(text)
                msg["text"] = text
                self._scroll_to_bottom()
                break

    def start_ai_message(self, generation_id=None):
        """Start a new AI message bubble for streaming."""
        self._flush_typing()
        self._active_generation_id = generation_id
        self._next_generation_seq = 0
        bubble = MessageBubble("", is_user=False)
        self._add_bubble(bubble)
        self._messages.append({"type": "ai", "text": "", "bubble": bubble})
        self._current_streaming_bubble = bubble
        self.status_indicator.set_state("generating")

    def stream_text(self, chunk, generation_id=None, seq=None):
        """Append text chunk to current streaming message."""
        if generation_id is not None:
            if generation_id != self._active_generation_id:
                return False
            if seq is not None:
                if seq != self._next_generation_seq:
                    return False
                self._next_generation_seq += 1
        if self._current_streaming_bubble:
            bubble = self._current_streaming_bubble
            self._type_bubble = bubble
            self._type_buffer += chunk
            # History keeps the full received text; the bubble reveals it at
            # speaking pace.
            if self._messages:
                self._messages[-1]["text"] = bubble.full_text + self._type_buffer
            if not self._type_ticker.isActive():
                self._type_ticker.start(self.TYPE_TICK_MS)
            return True
        return False

    # ~4.5 characters per second matches the TTS pace; a large backlog speeds
    # up so the text never falls far behind the voice.
    TYPE_TICK_MS = 220

    def _reveal_tick(self):
        """Move a few buffered characters into the bubble (typewriter)."""
        bubble = self._type_bubble
        if bubble is None:
            self._type_ticker.stop()
            return
        backlog = len(self._type_buffer)
        if backlog:
            step = 1
            if backlog > 160:
                step = 4
            elif backlog > 80:
                step = 2
            bubble.append_text(self._type_buffer[:step])
            self._type_buffer = self._type_buffer[step:]
            self._scroll_to_bottom()
        if not self._type_buffer and self._type_finished:
            self._type_finished = False
            self._type_bubble = None
            self._type_ticker.stop()
            self.status_indicator.set_state("idle")
            self._scroll_to_bottom()

    def _flush_typing(self):
        """Reveal everything still buffered and stop the ticker."""
        if self._type_bubble is not None and self._type_buffer:
            self._type_bubble.append_text(self._type_buffer)
        self._type_buffer = ""
        self._type_finished = False
        self._type_bubble = None
        if getattr(self, "_type_ticker", None) is not None:
            self._type_ticker.stop()

    def finish_streaming(self, generation_id=None):
        """Mark streaming as complete; buffered text keeps revealing."""
        if generation_id is not None and generation_id != self._active_generation_id:
            return False
        self._current_streaming_bubble = None
        self._active_generation_id = None
        self._next_generation_seq = 0
        self._type_finished = True
        if not self._type_buffer:
            # Nothing left to type out (e.g. a cancelled or empty reply).
            self._type_finished = False
            self._type_bubble = None
            self._type_ticker.stop()
            self.status_indicator.set_state("idle")
            self._scroll_to_bottom()
        return True

    def set_ai_status(self, status):
        """External control: 'idle', 'thinking', 'generating', 'speaking'."""
        self.status_indicator.set_state(status)

    def _add_bubble(self, bubble):
        """Append one message row (bubble + stretch) after the top spacer.

        Bubbles used to be added straight into the vertical layout, so they
        stretched to the full panel width and long replies looked like bars.
        Each bubble now sits in its own row, capped at 72% of the viewport.
        """
        if getattr(bubble, "_is_system", False):
            self._msg_layout.addWidget(bubble)
        else:
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(0)
            text = getattr(bubble, "_full_text", "") or ""
            bubble.setMinimumWidth(self._bubble_width_for(text))
            bubble.setMaximumWidth(self._bubble_max_width())
            if getattr(bubble, "_is_user", False):
                row_layout.addStretch(1)
                row_layout.addWidget(bubble)
            else:
                row_layout.addWidget(bubble)
                row_layout.addStretch(1)
            self._msg_layout.addWidget(row)
        self._scroll_to_bottom()

    def _bubble_max_width(self) -> int:
        """Widest a single message may grow, relative to the viewport."""
        width = self.scroll_area.viewport().width() if getattr(self, "scroll_area", None) else 600
        return max(320, int(width * 0.68))

    def _bubble_width_for(self, text: str) -> int:
        """Comfortable width for one message: long text gets room, short stays tight.

        A word-wrapped label reports a narrow size hint, so without a floor a
        two-line reply collapsed into a thin column; without the ceiling a long
        paragraph spanned the whole panel.
        """
        limit = self._bubble_max_width()
        preferred = int(len(text) * 14) + 36
        return max(240, min(limit, preferred))

    def resizeEvent(self, event):  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        limit = self._bubble_max_width()
        for msg in getattr(self, "_messages", []):
            bubble = msg.get("bubble")
            if bubble is not None:
                bubble.setMinimumWidth(self._bubble_width_for(getattr(bubble, "_full_text", "") or ""))
                bubble.setMaximumWidth(limit)

    def _scroll_to_bottom(self):
        """Keep newest message visible at the bottom.

        Two staggered scrolls (not four) cover layout settling after async
        streaming; auto-pin on range change handles the rest.
        """
        def do_scroll():
            self._msg_container.adjustSize()
            self._msg_container.updateGeometry()
            bar = self.scroll_area.verticalScrollBar()
            bar.setValue(bar.maximum())

        QTimer.singleShot(0, do_scroll)
        QTimer.singleShot(150, do_scroll)

    def _on_scroll_range_changed(self, _min, max_value):
        """Auto-pin to bottom only if user was already at/near the bottom."""
        bar = self.scroll_area.verticalScrollBar()
        # Only auto-scroll if user was already near the bottom (within 50px)
        if bar.value() >= bar.maximum() - 50:
            bar.setValue(max_value)

    def _on_send(self):
        text = self.text_input.toPlainText().strip()
        if text:
            self.text_submitted.emit(text)
            self.text_input.clear()

    def set_input_enabled(self, enabled):
        self.text_input.setEnabled(enabled)
        self.btn_send.setEnabled(enabled)

    def _on_clear(self):
        # Same behavior as the bottom "清除记录" button: delegate to the
        # MainWindow handler (which clears chat + starts a fresh greeting),
        # avoiding a redundant double clear.
        self.clear_clicked.emit()

    def clear_chat(self):
        """Remove every message row (bubbles now live inside wrapper rows)."""
        self._flush_typing()
        # Deleting only the bubble left its row alive with a stale snapshot,
        # so clear the layout itself and keep the leading spacer.
        while self._msg_layout.count() > 1:
            item = self._msg_layout.takeAt(1)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._messages.clear()
        self._current_streaming_bubble = None
        self._active_generation_id = None
        self.status_indicator.set_state("idle")
