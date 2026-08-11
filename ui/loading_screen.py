# Loading Screen - Centered frosted card with progress bar and animation

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect
)
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QFont, QColor


class LoadingScreen(QWidget):
    """Loading screen overlay with frosted glass card."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("loadingOverlay")
        self._dot_index = 0
        self._setup_ui()
        self._start_dot_animation()

    def _setup_ui(self):
        # Full overlay - semi-transparent background
        self.setStyleSheet("""
            #loadingOverlay {
                background-color: rgba(0, 0, 0, 0.15);
            }
        """)

        # Center the card
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)

        # Frosted card
        self.card = QFrame()
        self.card.setObjectName("loadingCard")
        self.card.setFixedSize(420, 300)
        self.card.setStyleSheet("""
            #loadingCard {
                background-color: rgba(245, 245, 247, 0.95);
                border-radius: 20px;
                border: 1px solid rgba(208, 208, 213, 0.5);
            }
        """)

        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 80))
        self.card.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(0)

        # Logo
        self.logo_label = QLabel("🎙️")
        self.logo_label.setFont(QFont("Segoe UI Emoji", 42))
        self.logo_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.logo_label)
        layout.addSpacing(8)

        # Title
        self.title_label = QLabel("小薇")
        self.title_label.setObjectName("loadingTitle")
        self.title_label.setFont(QFont("Microsoft YaHei", 20, QFont.Bold))
        self.title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title_label)

        # Subtitle
        self.subtitle_label = QLabel("AI 语音对话助手")
        self.subtitle_label.setObjectName("loadingSubtitle")
        self.subtitle_label.setFont(QFont("Microsoft YaHei", 10))
        self.subtitle_label.setStyleSheet("color: #7f8c8d;")
        self.subtitle_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.subtitle_label)
        layout.addSpacing(18)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 3px;
                background-color: #e0e0e5;
            }
            QProgressBar::chunk {
                background-color: #2d5a27;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.progress_bar)
        layout.addSpacing(12)

        # Step text
        self.step_label = QLabel("准备中...")
        self.step_label.setObjectName("loadingStep")
        self.step_label.setFont(QFont("Microsoft YaHei", 11))
        self.step_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.step_label)

        # Status text
        self.status_label = QLabel("")
        self.status_label.setObjectName("loadingStatus")
        self.status_label.setFont(QFont("Microsoft YaHei", 9))
        self.status_label.setStyleSheet("color: #95a5a6;")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        outer.addWidget(self.card)

        # Dot animation timer
        self._dot_timer = QTimer(self)
        self._dot_timer.timeout.connect(self._animate_dots)
        self._dots = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def _start_dot_animation(self):
        self._dot_timer.start(100)

    def _animate_dots(self):
        self._dot_index = (self._dot_index + 1) % len(self._dots)
        step_text = self.step_label.text()
        if "加载完成" not in step_text:
            self.logo_label.setText(f"🎙️ {self._dots[self._dot_index]}")
        else:
            self.logo_label.setText("✨ 🎙️ ✨")

    def set_progress(self, value):
        self.progress_bar.setValue(value)

    def set_step(self, text):
        self.step_label.setText(text)

    def set_status(self, text):
        self.status_label.setText(text)

    def set_loading_complete(self):
        self._dot_timer.stop()
        self.logo_label.setText("✨ 🎙️ ✨")
        self.step_label.setText("✅ 加载完成！")
        self.progress_bar.setValue(100)

    def fade_out(self, callback=None):
        """Fade out and remove the loading screen.

        Uses a QGraphicsOpacityEffect because this is a plain QWidget child
        (not a top-level window), so animating the `windowOpacity` property
        has no effect.
        """
        self._dot_timer.stop()
        opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(opacity)
        self._fade_anim = QPropertyAnimation(opacity, b"opacity")
        self._fade_anim.setDuration(500)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.InQuad)
        if callback:
            self._fade_anim.finished.connect(callback)
        self._fade_anim.start()
