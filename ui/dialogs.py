# Dialogs - Session end, feedback, crisis resources, continue/end choice

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QTextEdit, QFormLayout, QGroupBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont


class BaseDialog(QDialog):
    """Base dialog with frosted glass style."""

    def __init__(self, parent=None, title=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setStyleSheet("""
            QDialog {
                background-color: #f9f9f9;
            }
            QLabel {
                color: #2c3e50;
            }
            QPushButton {
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 12px;
            }
        """)


class SessionEndDialog(BaseDialog):
    """Dialog shown when a session ends."""

    def __init__(self, parent=None, end_type="", feedback="", relaxation_rec="",
                 report_path=None, play_audio=True):
        super().__init__(parent, "会话结束")
        self.setMinimumSize(450, 350)
        self._report_path = report_path
        self._setup_ui(end_type, feedback, relaxation_rec)

    def _setup_ui(self, end_type, feedback, relaxation_rec):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # Title
        title = QLabel("会话已结束")
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #2d5a27;")
        layout.addWidget(title)

        # End type
        type_label = QLabel(f"结束类型: {end_type}")
        type_label.setAlignment(Qt.AlignCenter)
        type_label.setStyleSheet("color: #7f8c8d; font-size: 12px;")
        layout.addWidget(type_label)

        # Feedback
        if feedback:
            feedback_group = QGroupBox("会话反馈")
            feedback_group.setStyleSheet("""
                QGroupBox {
                    font-weight: bold; color: #2d5a27;
                    border: 1px solid #d0d0d0; border-radius: 8px;
                    margin-top: 8px; padding-top: 16px;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 12px; padding: 0 6px;
                }
            """)
            fb_layout = QVBoxLayout(feedback_group)
            fb_label = QLabel(feedback)
            fb_label.setWordWrap(True)
            fb_label.setStyleSheet("color: #2c3e50; font-size: 12px;")
            fb_layout.addWidget(fb_label)
            layout.addWidget(feedback_group)

        # Relaxation recommendation
        if relaxation_rec:
            rec_label = QLabel(f"放松建议: {relaxation_rec}")
            rec_label.setWordWrap(True)
            rec_label.setStyleSheet("color: #8b7355; font-size: 12px; font-style: italic;")
            layout.addWidget(rec_label)

        # Report path
        if report_path:
            rp_label = QLabel(f"报告已保存: {report_path}")
            rp_label.setWordWrap(True)
            rp_label.setStyleSheet("color: #1565C0; font-size: 11px;")
            layout.addWidget(rp_label)

        layout.addStretch()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_new = QPushButton("开始新会话")
        btn_new.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50; color: white;
                border: 1px solid #388E3C; font-weight: bold;
            }
            QPushButton:hover { background-color: #388E3C; }
        """)
        btn_new.clicked.connect(self.accept)
        btn_layout.addWidget(btn_new)

        btn_close = QPushButton("关闭")
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: #f0f0f0; color: #2c3e50;
                border: 1px solid #d0d0d0;
            }
            QPushButton:hover { background-color: #e0e0e0; }
        """)
        btn_close.clicked.connect(self.reject)
        btn_layout.addWidget(btn_close)

        layout.addLayout(btn_layout)


class CrisisDialog(BaseDialog):
    """Crisis resources dialog with hotline numbers."""

    def __init__(self, parent=None, hotlines=None):
        super().__init__(parent, "危机热线")
        self.setMinimumSize(400, 300)
        self._setup_ui(hotlines or [])

    def _setup_ui(self, hotlines):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("重要提醒")
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #c0392b;")
        layout.addWidget(title)

        msg = QLabel("如果你正在经历危机，请立即联系以下热线：")
        msg.setWordWrap(True)
        msg.setStyleSheet("color: #2c3e50; font-size: 13px;")
        layout.addWidget(msg)

        for name, number in hotlines:
            hl = QLabel(f"  {name}: {number}")
            hl.setStyleSheet("color: #c0392b; font-size: 14px; font-weight: bold; padding: 4px;")
            layout.addWidget(hl)

        layout.addStretch()

        btn = QPushButton("我知道了")
        btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50; color: white;
                border: 1px solid #388E3C; font-weight: bold;
                padding: 8px 20px;
            }
            QPushButton:hover { background-color: #388E3C; }
        """)
        btn.clicked.connect(self.accept)
        layout.addWidget(btn, alignment=Qt.AlignCenter)


class ContinueOrEndDialog(BaseDialog):
    """Dialog asking user to continue or end the session."""

    continue_chosen = Signal()
    end_chosen = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, "继续或结束")
        self.setMinimumSize(380, 200)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        title = QLabel("放松训练已完成")
        title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #2d5a27;")
        layout.addWidget(title)

        msg = QLabel("请问您想要继续对话，还是会话到此结束？")
        msg.setAlignment(Qt.AlignCenter)
        msg.setStyleSheet("color: #2c3e50; font-size: 12px;")
        layout.addWidget(msg)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(20)

        btn_continue = QPushButton("继续对话")
        btn_continue.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50; color: white;
                border: 1px solid #388E3C; font-weight: bold;
                padding: 10px 24px; font-size: 13px;
            }
            QPushButton:hover { background-color: #388E3C; }
        """)
        btn_continue.clicked.connect(self._on_continue)
        btn_layout.addWidget(btn_continue)

        btn_end = QPushButton("结束会话")
        btn_end.setStyleSheet("""
            QPushButton {
                background-color: #FF9800; color: white;
                border: 1px solid #F57C00; font-weight: bold;
                padding: 10px 24px; font-size: 13px;
            }
            QPushButton:hover { background-color: #F57C00; }
        """)
        btn_end.clicked.connect(self._on_end)
        btn_layout.addWidget(btn_end)

        layout.addLayout(btn_layout)

    def _on_continue(self):
        self.continue_chosen.emit()
        self.accept()

    def _on_end(self):
        self.end_chosen.emit()
        self.accept()


class WarningDialog(BaseDialog):
    """Simple warning/info dialog."""

    def __init__(self, parent=None, title="提示", message=""):
        super().__init__(parent, title)
        self.setMinimumSize(350, 150)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        msg = QLabel(message)
        msg.setWordWrap(True)
        msg.setStyleSheet("color: #2c3e50; font-size: 12px;")
        layout.addWidget(msg)

        layout.addStretch()

        btn = QPushButton("确定")
        btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50; color: white;
                border: 1px solid #388E3C; font-weight: bold;
                padding: 6px 20px;
            }
            QPushButton:hover { background-color: #388E3C; }
        """)
        btn.clicked.connect(self.accept)
        layout.addWidget(btn, alignment=Qt.AlignCenter)


class FeedbackDialog(BaseDialog):
    """Dialog for collecting visitor feedback."""

    def __init__(self, parent=None):
        super().__init__(parent, "会话反馈")
        self.setMinimumSize(400, 300)
        self._feedback_text = ""
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("请提供您的反馈")
        title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #2d5a27;")
        layout.addWidget(title)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("请输入您对本次会话的感受和建议...")
        self.text_edit.setStyleSheet("""
            QTextEdit {
                background-color: white;
                border: 1px solid #d0d0d0;
                border-radius: 8px;
                padding: 8px;
                font-size: 12px;
            }
        """)
        layout.addWidget(self.text_edit)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_submit = QPushButton("提交")
        btn_submit.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50; color: white;
                border: 1px solid #388E3C; font-weight: bold;
                padding: 6px 20px;
            }
            QPushButton:hover { background-color: #388E3C; }
        """)
        btn_submit.clicked.connect(self._on_submit)
        btn_layout.addWidget(btn_submit)

        btn_skip = QPushButton("跳过")
        btn_skip.setStyleSheet("""
            QPushButton {
                background-color: #f0f0f0; color: #2c3e50;
                border: 1px solid #d0d0d0;
                padding: 6px 20px;
            }
        """)
        btn_skip.clicked.connect(self.accept)
        btn_layout.addWidget(btn_skip)

        layout.addLayout(btn_layout)

    def _on_submit(self):
        self._feedback_text = self.text_edit.toPlainText()
        self.accept()

    def get_feedback(self):
        return self._feedback_text
