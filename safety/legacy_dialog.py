"""Legacy crisis dialog retained outside the production UI package.

This module is intentionally not imported by the runtime UI.
"""

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from ui.dialogs import BaseDialog


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
