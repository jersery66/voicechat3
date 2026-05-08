# Control Panel - Left side panel with user info, recording, relaxation buttons

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QLineEdit, QComboBox, QFormLayout, QScrollArea,
    QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont

from .widgets import FrostedPanel, RecordButton, BlinkButton


class ControlPanel(FrostedPanel):
    """Left control panel with user info, recording, and relaxation controls."""

    # Signals
    confirm_user = Signal(dict)       # User info confirmed
    modify_user = Signal()            # Modify user info
    record_started = Signal()
    record_stopped = Signal()
    play_breathing = Signal()
    play_muscle = Signal()
    play_meditation = Signal()
    play_game = Signal()

    def __init__(self, parent=None):
        super().__init__(alpha=0.65, radius=16, parent=None)
        self.setObjectName("controlPanel")
        self.setFixedWidth(280)
        self._info_confirmed = False
        self._setup_ui()

    def _setup_ui(self):
        # Scroll area for the entire panel
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Logo
        logo_org = QLabel("南昌市强制隔离戒毒所\n心理矫治中心")
        logo_org.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        logo_org.setAlignment(Qt.AlignCenter)
        logo_org.setStyleSheet("color: #6d5a3a; padding: 6px 2px 2px 2px;")
        layout.addWidget(logo_org)

        subtitle = QLabel("AI 心理咨询语音系统")
        subtitle.setFont(QFont("Microsoft YaHei", 9))
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #9a8a6a; padding-bottom: 6px;")
        layout.addWidget(subtitle)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #d0d0d0;")
        layout.addWidget(sep)

        # User info section
        info_title = QLabel("基本信息（必填）")
        info_title.setObjectName("sectionTitle")
        info_title.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        info_title.setStyleSheet("color: #2d5a27; padding: 4px 0;")
        layout.addWidget(info_title)

        # Form
        form = QFormLayout()
        form.setSpacing(4)
        form.setContentsMargins(0, 0, 0, 0)

        self.id_input = QLineEdit()
        self.id_input.setPlaceholderText("被试编号")
        form.addRow("编号:", self.id_input)

        combo_style = """
            QComboBox {
                background-color: rgba(255, 248, 235, 0.9);
                border: 1px solid #c4a96a;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
                color: #4a3c1e;
            }
            QComboBox:focus { border: 1px solid #8b7355; }
            QComboBox::drop-down {
                border: none;
                width: 22px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #8b7355;
                margin-right: 6px;
            }
            QComboBox QAbstractItemView {
                background-color: #fdf6e3;
                border: 1px solid #c4a96a;
                selection-background-color: #d4b87a;
                selection-color: #3a2e14;
                color: #4a3c1e;
                outline: none;
                padding: 2px;
            }
            QComboBox QAbstractItemView::item {
                padding: 4px 8px;
                min-height: 22px;
            }
        """

        self.gender_combo = QComboBox()
        self.gender_combo.addItems(["", "男", "女"])
        self.gender_combo.setStyleSheet(combo_style)
        form.addRow("性别:", self.gender_combo)

        self.age_input = QLineEdit()
        self.age_input.setPlaceholderText("年龄")
        form.addRow("年龄:", self.age_input)

        self.edu_combo = QComboBox()
        self.edu_combo.addItems(["", "小学", "初中", "高中/中专", "大专", "本科及以上"])
        self.edu_combo.setStyleSheet(combo_style)
        form.addRow("文化程度:", self.edu_combo)

        self.marital_combo = QComboBox()
        self.marital_combo.addItems(["", "未婚", "已婚", "离异", "丧偶"])
        self.marital_combo.setStyleSheet(combo_style)
        form.addRow("婚姻状况:", self.marital_combo)

        self.drug_combo = QComboBox()
        self.drug_combo.addItems(["", "冰毒", "海洛因", "大麻", "K粉", "摇头丸", "混合", "其他"])
        self.drug_combo.setStyleSheet(combo_style)
        form.addRow("毒品类型:", self.drug_combo)

        layout.addLayout(form)

        # Confirm / Modify buttons
        self.btn_confirm = QPushButton("确认信息并开始")
        self.btn_confirm.setObjectName("primaryButton")
        self.btn_confirm.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50; color: white;
                border: 1px solid #388E3C; border-radius: 8px;
                padding: 6px; font-weight: bold; font-size: 12px;
            }
            QPushButton:hover { background-color: #388E3C; }
        """)
        self.btn_confirm.clicked.connect(self._on_confirm)
        layout.addWidget(self.btn_confirm)

        self.btn_modify = QPushButton("修改信息 / 新会话")
        self.btn_modify.setEnabled(False)
        self.btn_modify.setStyleSheet("""
            QPushButton {
                background-color: #ddd; color: #999;
                border: 1px solid #ccc; border-radius: 8px;
                padding: 5px; font-size: 11px;
            }
        """)
        self.btn_modify.clicked.connect(self._on_modify)
        layout.addWidget(self.btn_modify)

        # Status label for user info
        self.user_status_label = QLabel("请填写基本信息后开始对话")
        self.user_status_label.setStyleSheet("color: #E53935; font-size: 11px; padding: 2px;")
        layout.addWidget(self.user_status_label)

        # Separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color: #d0d0d0;")
        layout.addWidget(sep2)

        # Recording section
        rec_layout = QHBoxLayout()
        rec_layout.setAlignment(Qt.AlignCenter)

        self.record_button = RecordButton()
        self.record_button.setEnabled(False)
        self.record_button.started.connect(self.record_started.emit)
        self.record_button.stopped.connect(self.record_stopped.emit)
        rec_layout.addWidget(self.record_button)

        layout.addLayout(rec_layout)

        self.rec_hint_label = QLabel("点击麦克风开始录音")
        self.rec_hint_label.setAlignment(Qt.AlignCenter)
        self.rec_hint_label.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        layout.addWidget(self.rec_hint_label)

        # Separator
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.HLine)
        sep3.setStyleSheet("color: #d0d0d0;")
        layout.addWidget(sep3)

        # Relaxation section
        relax_title = QLabel("放松训练")
        relax_title.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        relax_title.setStyleSheet("color: #333; padding: 4px;")
        layout.addWidget(relax_title)

        self.btn_breathing = BlinkButton("🌬 呼吸放松训练")
        self.btn_breathing.setObjectName("relaxButton")
        self.btn_breathing._base_color = "#64B5F6"
        self.btn_breathing.setStyleSheet("""
            QPushButton {
                background-color: #64B5F6; color: white;
                border: none; border-radius: 8px;
                padding: 7px; font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #42A5F5; }
            QPushButton:disabled { background-color: #64B5F6; color: rgba(255,255,255,0.7); }
        """)
        self.btn_breathing.clicked.connect(self.play_breathing.emit)
        layout.addWidget(self.btn_breathing)

        self.btn_muscle = BlinkButton("💪 肌肉放松训练")
        self.btn_muscle.setObjectName("relaxButton")
        self.btn_muscle._base_color = "#4DD0E1"
        self.btn_muscle.setStyleSheet("""
            QPushButton {
                background-color: #4DD0E1; color: white;
                border: none; border-radius: 8px;
                padding: 7px; font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #26C6DA; }
            QPushButton:disabled { background-color: #4DD0E1; color: rgba(255,255,255,0.7); }
        """)
        self.btn_muscle.clicked.connect(self.play_muscle.emit)
        layout.addWidget(self.btn_muscle)

        self.btn_meditation = BlinkButton("🧘 冥想放松训练")
        self.btn_meditation.setObjectName("relaxButton")
        self.btn_meditation._base_color = "#9575CD"
        self.btn_meditation.setStyleSheet("""
            QPushButton {
                background-color: #9575CD; color: white;
                border: none; border-radius: 8px;
                padding: 7px; font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #7E57C2; }
            QPushButton:disabled { background-color: #9575CD; color: rgba(255,255,255,0.7); }
        """)
        self.btn_meditation.clicked.connect(self.play_meditation.emit)
        layout.addWidget(self.btn_meditation)

        self.btn_game = BlinkButton("🎮 心理互动游戏")
        self.btn_game.setObjectName("relaxButton")
        self.btn_game._base_color = "#FF8A65"
        self.btn_game.setStyleSheet("""
            QPushButton {
                background-color: #FF8A65; color: white;
                border: none; border-radius: 8px;
                padding: 7px; font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #FF7043; }
            QPushButton:disabled { background-color: #FF8A65; color: rgba(255,255,255,0.7); }
        """)
        self.btn_game.clicked.connect(self.play_game.emit)
        layout.addWidget(self.btn_game)

        # Store blink buttons for easy access
        self._blink_buttons = [
            self.btn_breathing, self.btn_muscle,
            self.btn_meditation, self.btn_game
        ]

        # Spacer
        layout.addStretch()

        # Separator
        sep4 = QFrame()
        sep4.setFrameShape(QFrame.HLine)
        sep4.setStyleSheet("color: #d0d0d0;")
        layout.addWidget(sep4)

        # Status label
        self.status_label = QLabel("正在初始化...")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #1565C0; font-size: 11px; padding: 4px;")
        layout.addWidget(self.status_label)

        scroll.setWidget(container)

        # Main layout for the panel
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def _on_confirm(self):
        user_id = self.id_input.text().strip()
        if not user_id:
            self.user_status_label.setText("请填写编号")
            return

        info = {
            "user_id": user_id,
            "gender": self.gender_combo.currentText(),
            "age": self.age_input.text().strip(),
            "education": self.edu_combo.currentText(),
            "marital": self.marital_combo.currentText(),
            "drug_type": self.drug_combo.currentText(),
        }

        self._info_confirmed = True
        self.user_status_label.setText(f"当前用户: {user_id}")
        self.user_status_label.setStyleSheet("color: #4CAF50; font-size: 11px; padding: 2px;")
        self.btn_confirm.setEnabled(False)
        self.btn_modify.setEnabled(True)
        self.btn_modify.setStyleSheet("""
            QPushButton {
                background-color: #FF9800; color: white;
                border: 1px solid #F57C00; border-radius: 8px;
                padding: 5px; font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background-color: #F57C00; }
        """)
        self.record_button.setEnabled(True)
        self.confirm_user.emit(info)

    def _on_modify(self):
        self._info_confirmed = False
        self.btn_confirm.setEnabled(True)
        self.btn_modify.setEnabled(False)
        self.btn_modify.setStyleSheet("""
            QPushButton {
                background-color: #ddd; color: #999;
                border: 1px solid #ccc; border-radius: 8px;
                padding: 5px; font-size: 11px;
            }
        """)
        self.record_button.setEnabled(False)
        self.user_status_label.setText("请填写基本信息后开始对话")
        self.user_status_label.setStyleSheet("color: #E53935; font-size: 11px; padding: 2px;")
        self.modify_user.emit()

    def get_user_info(self):
        return {
            "user_id": self.id_input.text().strip() or "default_user",
            "gender": self.gender_combo.currentText(),
            "age": self.age_input.text().strip(),
            "education": self.edu_combo.currentText(),
            "marital": self.marital_combo.currentText(),
            "drug_type": self.drug_combo.currentText(),
        }

    @property
    def info_confirmed(self):
        return self._info_confirmed

    def set_status(self, text):
        self.status_label.setText(text)

    def set_recording_state(self, recording):
        if recording:
            self.rec_hint_label.setText("点击停止录音")
        else:
            self.rec_hint_label.setText("点击麦克风开始录音")

    def reset_recording(self):
        if self.record_button.is_recording:
            self.record_button.stop_recording()
        self.rec_hint_label.setText("点击麦克风开始录音")

    def highlight_relax_button(self, btn_name):
        """Highlight a specific relaxation button with golden blink."""
        btn_map = {
            "breathing": self.btn_breathing,
            "muscle": self.btn_muscle,
            "meditation": self.btn_meditation,
            "game": self.btn_game,
        }
        btn = btn_map.get(btn_name)
        if btn:
            btn.start_blink()
            # Auto-stop after 10 seconds
            QTimer.singleShot(10000, btn.stop_blink)

    def stop_all_blinks(self):
        for btn in self._blink_buttons:
            btn.stop_blink()

    def set_buttons_enabled(self, enabled):
        self.record_button.setEnabled(enabled and self._info_confirmed)
        self.btn_breathing.setEnabled(enabled)
        self.btn_muscle.setEnabled(enabled)
        self.btn_meditation.setEnabled(enabled)
        self.btn_game.setEnabled(enabled)
