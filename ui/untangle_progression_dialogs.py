"""Small, non-competitive dialogs for Untangle campaign progression."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout


class UntangleCompletionDialog(QDialog):
    def __init__(self, *, level_number: int, total_levels: int = 15, final: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumWidth(300)
        self.setWindowTitle("全部关卡完成" if final else f"第 {level_number} 关完成")
        layout = QVBoxLayout(self)

        title = QLabel("全部关卡完成" if final else f"第 {level_number} 关完成")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #4f625f;")
        message = QLabel(
            f"你已经解开全部 {total_levels} 个关卡。" if final else "已经全部解开了。"
        )
        message.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        layout.addWidget(message)

        if final:
            self.continue_button = QPushButton("继续挑战")
            self.restart_button = QPushButton("从第1关重新开始")
            self.select_button = QPushButton("选择关卡")
            self.return_button = QPushButton("返回")
            buttons = (
                self.continue_button,
                self.restart_button,
                self.select_button,
                self.return_button,
            )
            self.continue_button.setDefault(True)
        else:
            self.next_button = QPushButton("下一关")
            self.replay_button = QPushButton("重玩本关")
            self.select_button = QPushButton("选择关卡")
            buttons = (self.next_button, self.replay_button, self.select_button)
            self.next_button.setDefault(True)

        for button in buttons:
            button.setAutoDefault(True)
            layout.addWidget(button)

        buttons[0].setFocus()


class UntangleSkipDialog(QDialog):
    def __init__(self, *, parent=None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumWidth(320)
        self.setWindowTitle("跳过这一关")
        layout = QVBoxLayout(self)
        message = QLabel("跳过这一关？之后仍可以从关卡选择中回来。")
        message.setWordWrap(True)
        message.setAlignment(Qt.AlignCenter)
        layout.addWidget(message)
        self.confirm_button = QPushButton("跳过并进入下一关")
        self.continue_button = QPushButton("继续这一关")
        self.confirm_button.setDefault(True)
        for button in (self.confirm_button, self.continue_button):
            button.setAutoDefault(True)
            layout.addWidget(button)
        self.confirm_button.setFocus()
