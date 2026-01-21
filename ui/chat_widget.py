# Chat Widget - Message Display and Streaming Text

import os
import sys
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, 
    QLabel, QPushButton, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont


class MessageBubble(QFrame):
    """Individual chat message bubble."""
    
    def __init__(self, text: str, is_user: bool = True, parent=None):
        super().__init__(parent)
        self.is_user = is_user
        self.full_text = text
        self.displayed_text = ""
        self.char_index = 0
        
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        
        # Message label
        self.text_label = QLabel()
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        # Apply style based on message type
        if self.is_user:
            self.text_label.setProperty("class", "userMessage")
            self.text_label.setStyleSheet("""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                                            stop:0 #6366F1, stop:1 #8B5CF6);
                border-radius: 16px 16px 4px 16px;
                padding: 12px 16px;
                color: #FFFFFF;
            """)
            layout.setAlignment(Qt.AlignmentFlag.AlignRight)
        else:
            self.text_label.setProperty("class", "assistantMessage")
            self.text_label.setStyleSheet("""
                background-color: rgba(45, 45, 65, 0.9);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 16px 16px 16px 4px;
                padding: 12px 16px;
                color: #E0E0E0;
            """)
            layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
            
        self.text_label.setText(self.full_text)
        layout.addWidget(self.text_label)
        
    def set_text(self, text: str):
        """Update the message text."""
        self.full_text = text
        self.text_label.setText(text)
        
    def append_text(self, text: str):
        """Append text to the message (for streaming)."""
        self.full_text += text
        self.text_label.setText(self.full_text)


class ChatWidget(QWidget):
    """Widget for displaying chat messages with streaming support."""
    
    message_added = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.messages = []
        self.current_streaming_bubble = None
        
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header
        header_layout = QHBoxLayout()
        
        title_label = QLabel("💬 对话")
        title_label.setObjectName("titleLabel")
        title_label.setFont(QFont("Microsoft YaHei", 18, QFont.Weight.Bold))
        header_layout.addWidget(title_label)
        
        header_layout.addStretch()
        
        # Clear button
        self.clear_button = QPushButton("🗑️ 清空")
        self.clear_button.setObjectName("clearButton")
        self.clear_button.clicked.connect(self.clear_chat)
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        header_layout.addWidget(self.clear_button)
        
        layout.addLayout(header_layout)
        
        # Scroll area for messages
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("chatScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        
        # Container for messages
        self.messages_container = QWidget()
        self.messages_layout = QVBoxLayout(self.messages_container)
        self.messages_layout.setContentsMargins(16, 16, 16, 16)
        self.messages_layout.setSpacing(12)
        self.messages_layout.addStretch()
        
        self.scroll_area.setWidget(self.messages_container)
        layout.addWidget(self.scroll_area)
        
    def add_user_message(self, text: str):
        """Add a user message bubble."""
        bubble = MessageBubble(text, is_user=True)
        self._add_bubble(bubble)
        self.messages.append({"type": "user", "text": text, "bubble": bubble})
        
    def add_assistant_message(self, text: str = ""):
        """Add an assistant message bubble and return it for streaming updates."""
        bubble = MessageBubble(text, is_user=False)
        self._add_bubble(bubble)
        self.messages.append({"type": "assistant", "text": text, "bubble": bubble})
        self.current_streaming_bubble = bubble
        return bubble
        
    def stream_text(self, text_chunk: str):
        """Append text to the current streaming message."""
        if self.current_streaming_bubble:
            self.current_streaming_bubble.append_text(text_chunk)
            # Update stored text
            if self.messages:
                self.messages[-1]["text"] = self.current_streaming_bubble.full_text
            self._scroll_to_bottom()
            
    def finish_streaming(self):
        """Mark streaming as complete."""
        self.current_streaming_bubble = None
        
    def _add_bubble(self, bubble: MessageBubble):
        """Add a bubble to the chat."""
        # Insert before the stretch
        count = self.messages_layout.count()
        self.messages_layout.insertWidget(count - 1, bubble)
        self._scroll_to_bottom()
        self.message_added.emit()
        
    def _scroll_to_bottom(self):
        """Scroll to the bottom of the chat."""
        QTimer.singleShot(50, lambda: self.scroll_area.verticalScrollBar().setValue(
            self.scroll_area.verticalScrollBar().maximum()
        ))
        
    def clear_chat(self):
        """Clear all messages."""
        for msg in self.messages:
            bubble = msg.get("bubble")
            if bubble:
                bubble.deleteLater()
        self.messages = []
        self.current_streaming_bubble = None
        
    def get_chat_history(self) -> list:
        """Get the chat history as a list of dicts."""
        return [{"type": msg["type"], "text": msg["text"]} for msg in self.messages]
