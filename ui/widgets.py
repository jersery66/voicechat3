# Custom Widgets - Frosted panels, animated buttons, message bubbles

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QGraphicsDropShadowEffect, QGraphicsBlurEffect,
    QSizePolicy
)
from PySide6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, QTimer, Signal,
    Property, QPoint, QSize, QParallelAnimationGroup
)
from PySide6.QtGui import (
    QFont, QColor, QPainter, QBrush, QPen, QLinearGradient,
    QPainterPath, QPixmap
)


class FrostedPanel(QFrame):
    """Semi-transparent frosted glass panel with rounded corners."""

    def __init__(self, alpha=0.85, radius=16, parent=None,
                 bg_color=(255, 255, 255), border_alpha=0.3):
        super().__init__(parent)
        self._alpha = alpha
        self._radius = radius
        self._bg_color = bg_color
        self._border_alpha = border_alpha
        self._dark_mode = False
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self._rebuild_style()

        # Drop shadow
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(shadow)

    def _rebuild_style(self):
        r, g, b = self._bg_color
        self.setStyleSheet(f"""
            FrostedPanel {{
                background-color: rgba({r}, {g}, {b}, {int(self._alpha * 255)});
                border-radius: {self._radius}px;
                border: 1px solid rgba(255, 255, 255, {self._border_alpha});
            }}
        """)

    def set_theme(self, dark: bool):
        """Switch between light and dark frost colors."""
        self._dark_mode = dark
        if dark:
            self._bg_color = (30, 30, 60)
            self._border_alpha = 0.08
        else:
            self._bg_color = (255, 255, 255)
            self._border_alpha = 0.3
        self._rebuild_style()


class AnimatedButton(QPushButton):
    """Button with press animation effect."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self._press_anim = None

    def mousePressEvent(self, event):
        self._animate_press()
        super().mousePressEvent(event)

    def _animate_press(self):
        self._press_anim = QPropertyAnimation(self, b"geometry")
        self._press_anim.setDuration(80)
        geo = self.geometry()
        shrink = 2
        self._press_anim.setStartValue(geo)
        self._press_anim.setKeyValueAt(0.5, geo.adjusted(shrink, shrink, -shrink, -shrink))
        self._press_anim.setEndValue(geo)
        self._press_anim.setEasingCurve(QEasingCurve.OutQuad)
        self._press_anim.start()


class BlinkButton(QPushButton):
    """Button with golden blink highlight for relaxation recommendations."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._toggle_blink)
        self._blink_phase = False
        self._is_blinking = False
        self._original_style = ""

    def start_blink(self):
        """Start golden blink animation."""
        if self._is_blinking:
            return
        self._is_blinking = True
        self._original_style = self.styleSheet()
        self._blink_phase = False
        # Immediate first blink, don't wait 500ms
        self._toggle_blink()
        self._blink_timer.start(500)

    def stop_blink(self):
        """Stop blink and restore original style."""
        self._is_blinking = False
        self._blink_timer.stop()
        self.setStyleSheet(self._original_style)

    def _toggle_blink(self):
        self._blink_phase = not self._blink_phase
        if self._blink_phase:
            self.setStyleSheet("""
                QPushButton {
                    border: 3px solid #FFD700;
                    background-color: #FFD54F;
                    color: #3E2723;
                    border-radius: 8px;
                    padding: 7px;
                    font-size: 12px;
                    font-weight: bold;
                }
            """)
        else:
            self.setStyleSheet(self._original_style)


class RecordButton(QPushButton):
    """Large circular recording button with pulse animation."""

    started = Signal()
    stopped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_recording = False
        self._pulse_phase = 0
        self.setFixedSize(80, 80)
        self.setCursor(Qt.PointingHandCursor)
        self._update_style()
        self.clicked.connect(self._toggle)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse)

    @property
    def is_recording(self):
        return self._is_recording

    def _toggle(self):
        if self._is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        self._is_recording = True
        self._pulse_timer.start(100)
        self._update_style()
        self.started.emit()

    def stop_recording(self):
        self._is_recording = False
        self._pulse_timer.stop()
        self._update_style()
        self.stopped.emit()

    def _pulse(self):
        self._pulse_phase = (self._pulse_phase + 1) % 10
        self.update()

    def _update_style(self):
        if self._is_recording:
            self.setText("⏹")
            self.setStyleSheet("""
                QPushButton {
                    background-color: #E53935;
                    color: white;
                    border-radius: 40px;
                    font-size: 28px;
                    border: 3px solid #C62828;
                }
                QPushButton:hover {
                    background-color: #C62828;
                }
            """)
        else:
            self.setText("🎤")
            self.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    border-radius: 40px;
                    font-size: 28px;
                    border: 3px solid #388E3C;
                }
                QPushButton:hover {
                    background-color: #388E3C;
                }
            """)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._is_recording:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            # Pulse ring effect
            alpha = int(80 + 80 * abs(self._pulse_phase / 5 - 1))
            pen = QPen(QColor(229, 57, 53, alpha))
            pen.setWidth(3)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            margin = 5 + self._pulse_phase
            painter.drawEllipse(self.rect().adjusted(margin, margin, -margin, -margin))
            painter.end()


class StatusIndicator(QWidget):
    """Small indicator showing AI state: thinking, generating, speaking."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "idle"
        self._anim_phase = 0
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._pulse)
        self.setFixedHeight(28)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)

        self._dot = QLabel("●")
        self._dot.setFont(QFont("Microsoft YaHei", 10))
        self._dot.setFixedWidth(16)

        self._label = QLabel("")
        self._label.setFont(QFont("Microsoft YaHei", 9))
        self._label.setStyleSheet("color: #8b7355;")

        layout.addStretch()
        layout.addWidget(self._dot)
        layout.addWidget(self._label)
        layout.addStretch()

        self.setVisible(False)
        self._update_style()

    def set_state(self, state: str):
        """Set AI state: 'idle', 'thinking', 'generating', 'speaking'."""
        self._state = state
        if state == "idle":
            self._anim_timer.stop()
            self.setVisible(False)
        else:
            self.setVisible(True)
            self._anim_phase = 0
            if not self._anim_timer.isActive():
                self._anim_timer.start(400)
        self._update_style()

    def _pulse(self):
        self._anim_phase = (self._anim_phase + 1) % 6
        self._update_style()

    def _update_style(self):
        phase = self._anim_phase
        if self._state == "thinking":
            dots = "●" * ((phase % 3) + 1) + "○" * (2 - (phase % 3))
            self._dot.setText(dots)
            self._dot.setStyleSheet("color: #64B5F6;")
            self._label.setText("正在理解...")
        elif self._state == "generating":
            alpha = int(180 + 75 * abs(phase / 3 - 1))
            self._dot.setText("✦")
            self._dot.setStyleSheet(f"color: rgba(76, 175, 80, {alpha});")
            self._label.setText("正在回复...")
        elif self._state == "speaking":
            bars = ["▁", "▃", "▅", "▇", "▅", "▃"]
            self._dot.setText(bars[phase])
            self._dot.setStyleSheet("color: #FF8A65;")
            self._label.setText("正在播放...")


class MessageBubble(QFrame):
    """Chat message bubble with user/AI/system styles. Supports dark mode."""

    _dark_mode = False

    @classmethod
    def set_dark_mode(cls, dark: bool):
        cls._dark_mode = dark

    def __init__(self, text="", is_user=True, is_system=False, parent=None):
        super().__init__(parent)
        self._full_text = text
        self._is_user = is_user
        self._is_system = is_system
        if not is_system:
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.text_label = QLabel(self._full_text)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.text_label.setFont(QFont("Microsoft YaHei", 11))

        dark = self._dark_mode
        if self._is_system:
            self.text_label.setAlignment(Qt.AlignCenter)
            self.text_label.setStyleSheet("""
                color: #78909c;
                font-style: italic;
                padding: 6px 12px;
                background: transparent;
            """)
            layout.setAlignment(Qt.AlignCenter)
        elif self._is_user:
            self.setStyleSheet(f"""
                background-color: {'#2d5a27' if dark else '#DCF8C6'};
                border-radius: 12px 12px 4px 12px;
            """)
            self.text_label.setAlignment(Qt.AlignRight)
            self.text_label.setStyleSheet(f"""
                color: {'#e0e0e0' if dark else '#2c3e50'};
                padding: 10px 14px;
                background: transparent;
            """)
        else:
            self.setStyleSheet(f"""
                background-color: {'rgba(40, 40, 70, 0.95)' if dark else 'rgba(255, 255, 255, 0.95)'};
                border: 1px solid {'#3a3a5c' if dark else '#e0e0e0'};
                border-radius: 12px 12px 12px 4px;
            """)
            self.text_label.setAlignment(Qt.AlignLeft)
            self.text_label.setStyleSheet(f"""
                color: {'#e0e0e0' if dark else '#2c3e50'};
                padding: 10px 14px;
                background: transparent;
            """)

        layout.addWidget(self.text_label)

    def set_text(self, text):
        self._full_text = text
        self.text_label.setText(text)

    def append_text(self, text):
        self._full_text += text
        self.text_label.setText(self._full_text)

    @property
    def full_text(self):
        return self._full_text
