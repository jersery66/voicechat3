# Control Panel - Recording and Status Controls

import os
import sys
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, 
    QLabel, QFrame, QProgressBar
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont, QIcon


class RecordButton(QPushButton):
    """Animated recording button with pulse effect."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_recording = False
        self.setObjectName("recordButton")
        self.setText("🎤")
        self.setFont(QFont("Segoe UI Emoji", 24))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(80, 80)
        
        # Pulse animation timer
        self.pulse_timer = QTimer(self)
        self.pulse_timer.timeout.connect(self._pulse)
        self.pulse_phase = 0
        
    def set_recording(self, recording: bool):
        """Set recording state and update appearance."""
        self.is_recording = recording
        self.setProperty("recording", str(recording).lower())
        self.style().unpolish(self)
        self.style().polish(self)
        
        if recording:
            self.setText("⏹️")
            self.pulse_timer.start(100)
        else:
            self.setText("🎤")
            self.pulse_timer.stop()
            self.setStyleSheet("")
            
    def _pulse(self):
        """Create pulse animation effect."""
        self.pulse_phase = (self.pulse_phase + 1) % 10
        opacity = 0.7 + 0.3 * (self.pulse_phase / 10)
        # The actual animation is handled by CSS, this just triggers updates


class StatusIndicator(QFrame):
    """Status indicator showing current state."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)
        
        # Status label
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)
        
        # Progress bar (for loading states)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # Indeterminate
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        
    def set_status(self, status: str, show_progress: bool = False):
        """Update the status display."""
        self.status_label.setText(status)
        if show_progress:
            self.progress_bar.show()
        else:
            self.progress_bar.hide()
            
    def set_idle(self):
        self.set_status("就绪", False)
        
    def set_recording(self):
        self.set_status("🔴 正在录音...", False)
        
    def set_transcribing(self):
        self.set_status("📝 正在识别...", True)
        
    def set_thinking(self):
        self.set_status("🤔 正在思考...", True)
        
    def set_speaking(self):
        self.set_status("🔊 正在播放...", False)


class ControlPanel(QWidget):
    """Control panel with recording button and status."""
    
    # Signals
    recording_started = pyqtSignal()
    recording_stopped = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("controlPanel")
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(16)
        
        # Left side - status
        self.status_indicator = StatusIndicator()
        layout.addWidget(self.status_indicator)
        
        layout.addStretch()
        
        # Center - record button
        self.record_button = RecordButton()
        self.record_button.clicked.connect(self._toggle_recording)
        layout.addWidget(self.record_button)
        
        layout.addStretch()
        
        # Right side - additional controls
        right_layout = QVBoxLayout()
        
        # Hint label
        self.hint_label = QLabel("点击麦克风开始录音")
        self.hint_label.setObjectName("statusLabel")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(self.hint_label)
        
        layout.addLayout(right_layout)
        
    def _toggle_recording(self):
        """Toggle recording state."""
        if self.record_button.is_recording:
            self.stop_recording()
        else:
            self.start_recording()
            
    def start_recording(self):
        """Start recording."""
        self.record_button.set_recording(True)
        self.status_indicator.set_recording()
        self.hint_label.setText("点击停止录音")
        self.recording_started.emit()
        
    def stop_recording(self):
        """Stop recording."""
        self.record_button.set_recording(False)
        self.status_indicator.set_transcribing()
        self.hint_label.setText("正在处理...")
        self.recording_stopped.emit()
        
    def set_status(self, status: str, show_progress: bool = False):
        """Set status display."""
        self.status_indicator.set_status(status, show_progress)
        
    def reset(self):
        """Reset to idle state."""
        self.record_button.set_recording(False)
        self.status_indicator.set_idle()
        self.hint_label.setText("点击麦克风开始录音")
        
    def set_enabled(self, enabled: bool):
        """Enable or disable the control panel."""
        self.record_button.setEnabled(enabled)
