# Settings Dialog - User ID, Background, and Model Settings

import os
import sys
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, 
    QLineEdit, QPushButton, QComboBox, QFileDialog, QFormLayout
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap

# Add parent directory to path for config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import OLLAMA_MODEL


class SettingsDialog(QDialog):
    """Settings dialog for user preferences."""
    
    settings_changed = pyqtSignal(dict)
    
    def __init__(self, parent=None, current_settings: dict = None):
        super().__init__(parent)
        self.current_settings = current_settings or {}
        self.new_settings = self.current_settings.copy()
        
        self.setWindowTitle("⚙️ 设置")
        self.setMinimumWidth(450)
        self.setModal(True)
        
        self._setup_ui()
        self._load_current_settings()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        
        # User Settings Group
        user_group = QGroupBox("👤 用户设置")
        user_layout = QFormLayout(user_group)
        
        self.user_id_input = QLineEdit()
        self.user_id_input.setPlaceholderText("请输入用户ID")
        user_layout.addRow("用户 ID:", self.user_id_input)
        
        layout.addWidget(user_group)
        
        # Appearance Group
        appearance_group = QGroupBox("🎨 外观设置")
        appearance_layout = QVBoxLayout(appearance_group)
        
        # Background image
        bg_layout = QHBoxLayout()
        bg_layout.addWidget(QLabel("聊天背景:"))
        
        self.bg_path_label = QLabel("未设置")
        self.bg_path_label.setStyleSheet("color: #9CA3AF;")
        bg_layout.addWidget(self.bg_path_label, 1)
        
        self.bg_choose_btn = QPushButton("选择图片")
        self.bg_choose_btn.clicked.connect(self._choose_background)
        bg_layout.addWidget(self.bg_choose_btn)
        
        self.bg_clear_btn = QPushButton("清除")
        self.bg_clear_btn.clicked.connect(self._clear_background)
        bg_layout.addWidget(self.bg_clear_btn)
        
        appearance_layout.addLayout(bg_layout)
        
        # Background preview
        self.bg_preview = QLabel()
        self.bg_preview.setFixedHeight(100)
        self.bg_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bg_preview.setStyleSheet("background-color: rgba(45, 45, 65, 0.5); border-radius: 8px;")
        appearance_layout.addWidget(self.bg_preview)
        
        layout.addWidget(appearance_group)
        
        # Model Settings Group
        model_group = QGroupBox("🤖 模型设置")
        model_layout = QFormLayout(model_group)
        
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems([
            "qwen2.5:7b",
            "qwen2.5:14b",
            "llama3:8b",
            "deepseek-r1:8b",
            "glm4:9b"
        ])
        model_layout.addRow("Ollama 模型:", self.model_combo)
        
        layout.addWidget(model_group)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        
        self.save_btn = QPushButton("保存")
        self.save_btn.clicked.connect(self._save_settings)
        self.save_btn.setDefault(True)
        button_layout.addWidget(self.save_btn)
        
        layout.addLayout(button_layout)
        
    def _load_current_settings(self):
        """Load current settings into the UI."""
        self.user_id_input.setText(self.current_settings.get("user_id", ""))
        
        bg_path = self.current_settings.get("background_image", "")
        if bg_path and os.path.exists(bg_path):
            self.bg_path_label.setText(os.path.basename(bg_path))
            self._update_bg_preview(bg_path)
        
        model = self.current_settings.get("ollama_model", OLLAMA_MODEL)
        index = self.model_combo.findText(model)
        if index >= 0:
            self.model_combo.setCurrentIndex(index)
        else:
            self.model_combo.setCurrentText(model)
            
    def _choose_background(self):
        """Open file dialog to choose background image."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择背景图片",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if file_path:
            self.new_settings["background_image"] = file_path
            self.bg_path_label.setText(os.path.basename(file_path))
            self._update_bg_preview(file_path)
            
    def _clear_background(self):
        """Clear background image."""
        self.new_settings["background_image"] = None
        self.bg_path_label.setText("未设置")
        self.bg_preview.clear()
        self.bg_preview.setText("无背景")
        
    def _update_bg_preview(self, path: str):
        """Update background preview."""
        if os.path.exists(path):
            pixmap = QPixmap(path)
            scaled = pixmap.scaled(
                self.bg_preview.width(), 
                self.bg_preview.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.bg_preview.setPixmap(scaled)
            
    def _save_settings(self):
        """Save settings and emit signal."""
        self.new_settings["user_id"] = self.user_id_input.text().strip()
        self.new_settings["ollama_model"] = self.model_combo.currentText()
        
        self.settings_changed.emit(self.new_settings)
        self.accept()
        
    def get_settings(self) -> dict:
        """Get the new settings."""
        return self.new_settings
