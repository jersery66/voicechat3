# Settings Dialog - User ID, Background, Model Settings, and User Profile

import os
import sys
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, 
    QLineEdit, QPushButton, QComboBox, QFileDialog, QFormLayout,
    QSpinBox, QTextEdit, QMessageBox, QListWidget, QListWidgetItem
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap

# Add parent directory to path for config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import OLLAMA_MODEL


class SettingsDialog(QDialog):
    """Settings dialog for user preferences and profile."""
    
    settings_changed = pyqtSignal(dict)
    
    def __init__(self, parent=None, current_settings: dict = None, data_manager=None):
        super().__init__(parent)
        self.current_settings = current_settings or {}
        self.new_settings = self.current_settings.copy()
        self.data_manager = data_manager
        
        self.setWindowTitle("⚙️ 设置")
        self.setMinimumWidth(500)
        self.setMinimumHeight(600)
        self.setModal(True)
        
        self._setup_ui()
        self._load_current_settings()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        
        user_group = QGroupBox("👤 来访者信息")
        user_layout = QVBoxLayout(user_group)
        
        subject_layout = QHBoxLayout()
        subject_layout.addWidget(QLabel("被试编号:"))
        self.user_id_input = QLineEdit()
        self.user_id_input.setPlaceholderText("如：被试001")
        subject_layout.addWidget(self.user_id_input)
        
        self.load_profile_btn = QPushButton("加载")
        self.load_profile_btn.setFixedWidth(60)
        self.load_profile_btn.clicked.connect(self._load_profile_by_id)
        subject_layout.addWidget(self.load_profile_btn)
        
        user_layout.addLayout(subject_layout)
        
        profile_form = QFormLayout()
        
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("来访者姓名")
        profile_form.addRow("姓名:", self.name_input)
        
        age_layout = QHBoxLayout()
        self.age_input = QSpinBox()
        self.age_input.setRange(0, 120)
        self.age_input.setValue(0)
        age_layout.addWidget(self.age_input)
        age_layout.addWidget(QLabel("岁"))
        age_layout.addStretch()
        profile_form.addRow("年龄:", age_layout)
        
        self.gender_input = QComboBox()
        self.gender_input.addItems(["", "男", "女"])
        self.gender_input.setEditable(True)
        profile_form.addRow("性别:", self.gender_input)
        
        self.occupation_input = QLineEdit()
        self.occupation_input.setPlaceholderText("如：无业、工人、个体户等")
        profile_form.addRow("职业:", self.occupation_input)
        
        self.addiction_type_input = QLineEdit()
        self.addiction_type_input.setPlaceholderText("如：冰毒、海洛因等")
        profile_form.addRow("吸毒类型:", self.addiction_type_input)
        
        duration_layout = QHBoxLayout()
        self.addiction_duration_input = QSpinBox()
        self.addiction_duration_input.setRange(0, 50)
        self.addiction_duration_input.setValue(0)
        duration_layout.addWidget(self.addiction_duration_input)
        duration_layout.addWidget(QLabel("年"))
        duration_layout.addStretch()
        profile_form.addRow("吸毒年限:", duration_layout)
        
        treatment_layout = QHBoxLayout()
        self.treatment_count_input = QSpinBox()
        self.treatment_count_input.setRange(0, 20)
        self.treatment_count_input.setValue(0)
        treatment_layout.addWidget(self.treatment_count_input)
        treatment_layout.addWidget(QLabel("次"))
        treatment_layout.addStretch()
        profile_form.addRow("戒毒次数:", treatment_layout)
        
        user_layout.addLayout(profile_form)
        
        notes_label = QLabel("备注:")
        user_layout.addWidget(notes_label)
        
        self.notes_input = QTextEdit()
        self.notes_input.setPlaceholderText("其他需要记录的信息...")
        self.notes_input.setMaximumHeight(80)
        user_layout.addWidget(self.notes_input)
        
        layout.addWidget(user_group)
        
        history_group = QGroupBox("📋 历史记录")
        history_layout = QVBoxLayout(history_group)
        
        self.history_list = QListWidget()
        self.history_list.setMaximumHeight(100)
        self.history_list.setAlternatingRowColors(True)
        history_layout.addWidget(self.history_list)
        
        self.refresh_history_btn = QPushButton("🔄 刷新历史记录")
        self.refresh_history_btn.clicked.connect(self._refresh_history)
        history_layout.addWidget(self.refresh_history_btn)
        
        layout.addWidget(history_group)
        
        appearance_group = QGroupBox("🎨 外观设置")
        appearance_layout = QVBoxLayout(appearance_group)
        
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
        
        self.bg_preview = QLabel()
        self.bg_preview.setFixedHeight(80)
        self.bg_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bg_preview.setStyleSheet("background-color: rgba(45, 45, 65, 0.5); border-radius: 8px;")
        appearance_layout.addWidget(self.bg_preview)
        
        layout.addWidget(appearance_group)
        
        model_group = QGroupBox("🤖 模型设置")
        model_layout = QFormLayout(model_group)
        
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems([
            "qwen2.5:7b",
            "qwen2.5:14b",
            "qwen2.5:32b",
            "qwen2.5:72b",
            "llama3:8b",
            "deepseek-r1:8b",
            "glm4:9b"
        ])
        model_layout.addRow("Ollama 模型:", self.model_combo)
        
        layout.addWidget(model_group)
        
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
            
        self._load_profile_by_id()
        self._refresh_history()
            
    def _load_profile_by_id(self):
        """Load user profile by subject ID."""
        subject_id = self.user_id_input.text().strip()
        if not subject_id or not self.data_manager:
            self._clear_profile_fields()
            return
            
        profile = self.data_manager.load_user_profile(subject_id)
        if profile:
            self.name_input.setText(profile.get("name", ""))
            self.age_input.setValue(profile.get("age", 0))
            self.gender_input.setCurrentText(profile.get("gender", ""))
            self.occupation_input.setText(profile.get("occupation", ""))
            self.addiction_type_input.setText(profile.get("addiction_type", ""))
            self.addiction_duration_input.setValue(profile.get("addiction_duration", 0))
            self.treatment_count_input.setValue(profile.get("treatment_count", 0))
            self.notes_input.setPlainText(profile.get("notes", ""))
        else:
            self._clear_profile_fields()
            
    def _clear_profile_fields(self):
        """Clear all profile fields."""
        self.name_input.clear()
        self.age_input.setValue(0)
        self.gender_input.setCurrentIndex(0)
        self.occupation_input.clear()
        self.addiction_type_input.clear()
        self.addiction_duration_input.setValue(0)
        self.treatment_count_input.setValue(0)
        self.notes_input.clear()
        
    def _refresh_history(self):
        """Refresh history list for current subject."""
        self.history_list.clear()
        subject_id = self.user_id_input.text().strip()
        if not subject_id or not self.data_manager:
            return
            
        summaries = self.data_manager.load_session_summaries(subject_id, limit=5)
        for s in summaries:
            date = s.get("date", "未知日期")
            summary_text = s.get("summary", "无摘要")
            display_text = f"{date}: {summary_text[:50]}..." if len(summary_text) > 50 else f"{date}: {summary_text}"
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, s)
            self.history_list.addItem(item)
            
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
        subject_id = self.user_id_input.text().strip()
        
        self.new_settings["user_id"] = subject_id
        self.new_settings["ollama_model"] = self.model_combo.currentText()
        
        self.new_settings["user_profile"] = {
            "name": self.name_input.text().strip(),
            "age": self.age_input.value(),
            "gender": self.gender_input.currentText(),
            "occupation": self.occupation_input.text().strip(),
            "addiction_type": self.addiction_type_input.text().strip(),
            "addiction_duration": self.addiction_duration_input.value(),
            "treatment_count": self.treatment_count_input.value(),
            "notes": self.notes_input.toPlainText().strip()
        }
        
        if self.data_manager and subject_id:
            self.data_manager.set_user_id(subject_id)
            self.data_manager.save_user_profile(self.new_settings["user_profile"])
        
        self.settings_changed.emit(self.new_settings)
        self.accept()
        
    def get_settings(self) -> dict:
        """Get the new settings."""
        return self.new_settings
