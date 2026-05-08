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

    def __init__(self, alpha=0.85, radius=16, parent=None):
        super().__init__(parent)
        self._alpha = alpha
        self._radius = radius
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet(f"""
            FrostedPanel {{
                background-color: rgba(255, 255, 255, {int(alpha * 255)});
                border-radius: {radius}px;
                border: 1px solid rgba(255, 255, 255, 0.3);
            }}
        """)

        # Drop shadow
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(shadow)


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
        self._blink_timer.start(500)

    def stop_blink(self):
        """Stop blink and restore original style."""
        self._is_blinking = False
        self._blink_timer.stop()
        self.setStyleSheet(self._original_style)

    def _toggle_blink(self):
        self._blink_phase = not self._blink_phase
        if self._blink_phase:
            self.setStyleSheet(f"""
                QPushButton {{
                    border: 2px solid #FFD700;
                    background-color: rgba(255, 215, 0, 0.3);
                    border-radius: 8px;
                }}
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


class MessageBubble(QFrame):
    """Chat message bubble with user/AI/system styles."""

    def __init__(self, text="", is_user=True, is_system=False, parent=None):
        super().__init__(parent)
        self._full_text = text
        self._is_user = is_user
        self._is_system = is_system
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)

        self.text_label = QLabel(self._full_text)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.text_label.setFont(QFont("Microsoft YaHei", 11))
        self.text_label.setMaximumWidth(340)

        if self._is_system:
            self.text_label.setAlignment(Qt.AlignCenter)
            self.text_label.setStyleSheet("""
                color: #95a5a6;
                font-style: italic;
                padding: 6px 12px;
                background: transparent;
            """)
            layout.setAlignment(Qt.AlignCenter)
        elif self._is_user:
            self.text_label.setStyleSheet("""
                background-color: #DCF8C6;
                color: #2c3e50;
                border-radius: 16px 16px 4px 16px;
                padding: 10px 14px;
            """)
            layout.setAlignment(Qt.AlignRight)
        else:
            self.text_label.setStyleSheet("""
                background-color: rgba(255, 255, 255, 0.95);
                color: #2c3e50;
                border: 1px solid #e0e0e0;
                border-radius: 16px 16px 16px 4px;
                padding: 10px 14px;
            """)
            layout.setAlignment(Qt.AlignLeft)

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
