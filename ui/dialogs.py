# Dialogs - Session end, feedback, crisis resources, continue/end choice

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QGroupBox
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
    """Crisis resources dialog with risk assessment and hotline numbers."""

    def __init__(self, parent=None, hotlines=None, risk_level=0, indicators=None):
        super().__init__(parent, "危机干预")
        self.setMinimumSize(420, 380)
        self._setup_ui(hotlines or [], risk_level, indicators or [])

    def _setup_ui(self, hotlines, risk_level, indicators):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        # Title — changes color based on risk level
        risk_color = "#c0392b" if risk_level >= 7 else "#e67e22"
        title = QLabel("⚠ 危机预警" if risk_level >= 7 else "风险提醒")
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color: {risk_color};")
        layout.addWidget(title)

        # Risk level bar (visual)
        if risk_level > 0:
            risk_text = QLabel(f"风险评估等级: {risk_level} / 10")
            risk_text.setAlignment(Qt.AlignCenter)
            risk_text.setStyleSheet(f"color: {risk_color}; font-size: 13px; font-weight: bold;")
            layout.addWidget(risk_text)

        # Indicators
        if indicators:
            ind_label = QLabel("检测到的风险信号:")
            ind_label.setStyleSheet("color: #555; font-size: 11px; font-weight: bold; margin-top: 4px;")
            layout.addWidget(ind_label)
            for ind in indicators:
                dot = QLabel(f"  • {ind}")
                dot.setStyleSheet("color: #c0392b; font-size: 11px; padding: 1px 8px;")
                layout.addWidget(dot)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("border: 1px solid #e0e0e0;")
        layout.addWidget(sep)

        # Hotlines
        msg = QLabel("如果你正在经历危机，请立即联系以下热线：")
        msg.setWordWrap(True)
        msg.setStyleSheet("color: #2c3e50; font-size: 12px;")
        layout.addWidget(msg)

        # Handle dict format (key=name, value=number)
        if isinstance(hotlines, dict):
            hotlines = [{"name": k, "number": v} for k, v in hotlines.items()]

        for item in hotlines:
            if isinstance(item, dict):
                name = item.get("name") or item.get("label") or item.get("title") or "危机热线"
                number = item.get("number") or item.get("phone") or item.get("value") or ""
            elif isinstance(item, (list, tuple)):
                if len(item) >= 2:
                    name = str(item[0])
                    number = " / ".join(str(x) for x in item[1:] if x)
                elif len(item) == 1:
                    name = "危机热线"
                    number = str(item[0])
                else:
                    continue
            else:
                name = "危机热线"
                number = str(item)

            hl = QLabel(f"  {name}: {number}")
            hl.setStyleSheet("color: #c0392b; font-size: 13px; font-weight: bold; padding: 2px 8px;")
            layout.addWidget(hl)

        # Timestamp
        from datetime import datetime
        ts = QLabel(f"记录时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        ts.setAlignment(Qt.AlignRight)
        ts.setStyleSheet("color: #999; font-size: 10px; margin-top: 4px;")
        layout.addWidget(ts)

        layout.addStretch()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        btn_notify = QPushButton("通知值班人员")
        btn_notify.setStyleSheet("""
            QPushButton {
                background-color: #E53935; color: white;
                border: 1px solid #C62828; font-weight: bold;
                padding: 8px 16px;
            }
            QPushButton:hover { background-color: #C62828; }
        """)
        btn_notify.clicked.connect(self._on_notify)
        btn_layout.addWidget(btn_notify)

        btn_ok = QPushButton("我知道了")
        btn_ok.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50; color: white;
                border: 1px solid #388E3C; font-weight: bold;
                padding: 8px 20px;
            }
            QPushButton:hover { background-color: #388E3C; }
        """)
        btn_ok.clicked.connect(self.accept)
        btn_layout.addWidget(btn_ok)

        layout.addLayout(btn_layout)

    def _on_notify(self):
        """Placeholder for staff notification."""
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(
            self, "通知",
            "已记录危机事件。\n\n（通知值班人员功能将在接入内部通讯系统后启用）"
        )


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

    def _setup_ui_for_timeout(self):
        """Rebuild UI for timeout ask-continue scenario."""
        for child in self.findChildren(QLabel):
            child.deleteLater()
        for child in self.findChildren(QPushButton):
            child.deleteLater()

        layout = self.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.layout():
                item.layout().deleteLater()
            elif item.widget():
                item.widget().deleteLater()

        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        title = QLabel("对话时间提醒")
        title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #5d4037;")
        layout.addWidget(title)

        msg = QLabel("我们的对话已进行约45分钟。请问您想继续聊，还是今天就到这里？")
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

