# Dialogs - Session end, feedback, continue/end choice

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
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



class ContinueOrEndDialog(BaseDialog):
    """Dialog asking user to continue or end the session.

    Builds the final layout exactly once in __init__ — the `timeout` flag only
    switches the title/text, never rebuilding children asynchronously (which
    previously left old/!new widgets briefly co-existing via deleteLater()).
    """

    continue_chosen = Signal()
    end_chosen = Signal()

    def __init__(self, parent=None, timeout=False):
        super().__init__(parent, "会话时间提醒" if timeout else "继续或结束")
        self.setMinimumSize(380, 200)
        self._timeout = timeout
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        if self._timeout:
            title_text = "对话时间提醒"
            title_color = "#5d4037;"
            msg_text = "我们的对话已进行约45分钟。请问您想继续聊，还是今天就到这里？"
        else:
            title_text = "放松训练已完成"
            title_color = "#2d5a27;"
            msg_text = "请问您想要继续对话，还是会话到此结束？"

        title = QLabel(title_text)
        title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color: {title_color};")
        layout.addWidget(title)

        msg = QLabel(msg_text)
        msg.setAlignment(Qt.AlignCenter)
        msg.setWordWrap(True)
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


class EndSessionDecisionDialog(BaseDialog):
    """End-session dialog that adapts options based on scale/relaxation completion."""

    continue_chosen = Signal()
    relax_chosen = Signal()
    end_chosen = Signal()
    cancel_chosen = Signal()

    def __init__(self, parent=None, state=None, recommended_tag=None):
        super().__init__(parent, "结束会话")
        self.setMinimumSize(420, 260)
        self._state = state or {}
        self._recommended_tag = recommended_tag or "breathing"
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 24)

        scale_inc = self._state.get("scale_incomplete", False)
        relax_done = self._state.get("relax_done", False)

        # Title & description based on state (no clinical jargon exposed to participant)
        if scale_inc and not relax_done:
            title_text = "确定要结束吗？"
            desc_text = "我们还没聊完，放松训练也还没做。\n你想怎么处理？"
        elif scale_inc and relax_done:
            title_text = "确定要结束吗？"
            desc_text = "放松训练做完了，不过我们还可以再聊聊。\n你想怎么处理？"
        elif not scale_inc and not relax_done:
            title_text = "确定要结束吗？"
            desc_text = "聊天部分结束了，结束前做个短放松吧？"
        else:
            title_text = "会话结束"
            desc_text = "今天的对话到这里，感谢你的参与。"

        title = QLabel(title_text)
        title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #2d5a27;")
        layout.addWidget(title)

        desc = QLabel(desc_text)
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #555; font-size: 12px; padding: 4px 0;")
        layout.addWidget(desc)

        # Buttons based on state
        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(8)

        tag_cn = {"breathing": "呼吸", "muscle": "肌肉", "meditation": "冥想"}.get(
            self._recommended_tag, self._recommended_tag
        )

        if scale_inc:
            btn_continue = QPushButton("继续聊天")
            btn_continue.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50; color: white;
                    border: 1px solid #388E3C; border-radius: 8px;
                    padding: 10px; font-size: 13px; font-weight: bold;
                }
                QPushButton:hover { background-color: #388E3C; }
            """)
            btn_continue.clicked.connect(self._on_continue)
            btn_layout.addWidget(btn_continue)

        if not relax_done:
            btn_relax = QPushButton(f"做{tag_cn}放松训练")
            btn_relax.setStyleSheet("""
                QPushButton {
                    background-color: #64B5F6; color: white;
                    border: none; border-radius: 8px;
                    padding: 10px; font-size: 13px; font-weight: bold;
                }
                QPushButton:hover { background-color: #42A5F5; }
            """)
            btn_relax.clicked.connect(self._on_relax)
            btn_layout.addWidget(btn_relax)

        btn_end = QPushButton("结束会话")
        btn_end.setStyleSheet("""
            QPushButton {
                background-color: #FF9800; color: white;
                border: 1px solid #F57C00; border-radius: 8px;
                padding: 10px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background-color: #F57C00; }
        """)
        btn_end.clicked.connect(self._on_end)
        btn_layout.addWidget(btn_end)

        btn_cancel = QPushButton("取消，继续对话")
        btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #f0f0f0; color: #2c3e50;
                border: 1px solid #d0d0d0; border-radius: 8px;
                padding: 8px; font-size: 12px;
            }
            QPushButton:hover { background-color: #e0e0e0; }
        """)
        btn_cancel.clicked.connect(self._on_cancel)
        btn_layout.addWidget(btn_cancel)

        layout.addLayout(btn_layout)

    def _on_continue(self):
        self.continue_chosen.emit()
        self.accept()

    def _on_relax(self):
        self.relax_chosen.emit()
        self.accept()

    def _on_end(self):
        self.end_chosen.emit()
        self.accept()

    def _on_cancel(self):
        self.cancel_chosen.emit()
        self.accept()

