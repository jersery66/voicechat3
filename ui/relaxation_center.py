"""Relaxation Center catalog and presentation shell.

The shell is catalog-driven and deliberately contains no Agent, policy, scale,
session, media-provider, or game business logic.  Core media and native game
execution are handed to the owning window through typed content signals.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from relaxation.catalog import RelaxationCatalog
from relaxation.contracts import RelaxationContentRole, RelaxationContentType, RelaxationState
from relaxation.runtime import RelaxationRuntime


class RelaxationCenterDialog(QDialog):
    """Catalog-driven Center shell for core content and native games."""

    core_content_requested = Signal(str)
    game_content_requested = Signal(str)
    returned_to_chat = Signal()

    def __init__(
        self,
        *,
        catalog: RelaxationCatalog,
        runtime: RelaxationRuntime,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.runtime = runtime
        self._closing_to_chat = False
        self.setWindowTitle("放松一下")
        self.setModal(False)
        self.setMinimumSize(560, 460)

        self.core_content_ids = tuple(
            item.id
            for item in catalog.list_by_role(
                RelaxationContentRole.CORE_RELAXATION,
                enabled_only=False,
            )
        )
        self.leisure_game_ids = tuple(
            item.id
            for item in catalog.list_by_role(RelaxationContentRole.LEISURE)
            if item.category is RelaxationContentType.GAME
        )
        self.core_buttons: dict[str, QPushButton] = {}
        self.game_buttons: dict[str, QPushButton] = {}
        self.preferred_core_content_id: str | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setStyleSheet(
            """
            QDialog { background-color: #f5f0e8; }
            QLabel { color: #4a3c2a; }
            QPushButton {
                background-color: #fffaf2;
                border: 1px solid #c4a96a;
                border-radius: 10px;
                padding: 12px;
                color: #4a3c2a;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #f5e7c8; }
            QPushButton:disabled { color: #a9a39a; background-color: #ebe7df; border-color: #d5d0c8; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        self.stack = QStackedWidget(self)
        self.center_page = self._build_center_page()
        self.games_page = self._build_games_page()
        self.stack.addWidget(self.center_page)
        self.stack.addWidget(self.games_page)
        root.addWidget(self.stack)

    def _build_center_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        title = QLabel("放松一下")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("Microsoft YaHei", 18, QFont.Bold))
        layout.addWidget(title)
        subtitle = QLabel("选一个现在比较想做的就可以。")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #8b8175; padding-bottom: 8px;")
        layout.addWidget(subtitle)
        self.center_status_label = QLabel("", page)
        self.center_status_label.setAlignment(Qt.AlignCenter)
        self.center_status_label.setStyleSheet("color: #9b6f58; padding-bottom: 6px;")
        layout.addWidget(self.center_status_label)

        core_heading = QLabel("核心放松")
        core_heading.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        layout.addWidget(core_heading)
        core_grid = QGridLayout()
        core_grid.setSpacing(10)
        for index, content_id in enumerate(self.core_content_ids):
            definition = self.catalog.require(content_id)
            button = QPushButton(definition.display_name, page)
            button.setObjectName(f"core_{content_id}")
            button.setMinimumHeight(64)
            button.setEnabled(definition.is_available)
            button.clicked.connect(
                lambda _checked=False, item_id=content_id: self.core_content_requested.emit(item_id)
            )
            self.core_buttons[content_id] = button
            core_grid.addWidget(button, index // 2, index % 2)
        layout.addLayout(core_grid)

        separator = QLabel("想单纯休息一会儿？")
        separator.setAlignment(Qt.AlignCenter)
        separator.setStyleSheet("color: #8b8175; padding: 12px 0 4px;")
        layout.addWidget(separator)
        leisure_row = QHBoxLayout()
        self.videos_button = QPushButton("看看视频\n内容整理中", page)
        self.videos_button.setObjectName("leisure_videos")
        self.videos_button.setEnabled(
            bool(
                self.catalog.list_by_role(RelaxationContentRole.LEISURE)
                and any(
                    item.category is RelaxationContentType.VIDEO
                    and item.is_available
                    for item in self.catalog.list_by_role(RelaxationContentRole.LEISURE)
                )
            )
        )
        leisure_row.addWidget(self.videos_button)
        self.games_button = QPushButton("玩一会儿", page)
        self.games_button.setObjectName("leisure_games")
        self.games_button.clicked.connect(lambda: self.stack.setCurrentWidget(self.games_page))
        leisure_row.addWidget(self.games_button)
        layout.addLayout(leisure_row)

        return_button = QPushButton("返回聊天", page)
        return_button.setObjectName("return_to_chat")
        return_button.clicked.connect(self.close_to_chat)
        layout.addStretch(1)
        layout.addWidget(return_button)
        return page

    def _build_games_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        title = QLabel("玩一会儿")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        layout.addWidget(title)
        hint = QLabel("可以随时结束，按自己的节奏玩一会儿。")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #8b8175; padding-bottom: 10px;")
        layout.addWidget(hint)
        self.games_status_label = QLabel("", page)
        self.games_status_label.setAlignment(Qt.AlignCenter)
        self.games_status_label.setStyleSheet("color: #6f8e83; padding-bottom: 6px;")
        layout.addWidget(self.games_status_label)
        grid = QGridLayout()
        grid.setSpacing(10)
        for index, content_id in enumerate(self.leisure_game_ids):
            definition = self.catalog.require(content_id)
            button = QPushButton(definition.display_name, page)
            button.setObjectName(f"game_{content_id}")
            button.setMinimumHeight(64)
            button.setEnabled(definition.is_available)
            button.clicked.connect(
                lambda _checked=False, item_id=content_id: self.game_content_requested.emit(item_id)
            )
            self.game_buttons[content_id] = button
            grid.addWidget(button, index // 2, index % 2)
        layout.addLayout(grid)
        layout.addStretch(1)
        self.games_back_button = QPushButton("返回放松中心", page)
        self.games_back_button.setObjectName("games_back")
        self.games_back_button.clicked.connect(lambda: self.stack.setCurrentWidget(self.center_page))
        layout.addWidget(self.games_back_button)
        return page

    def open_center(self) -> None:
        """Enter the Center once and show/activate the existing shell."""
        state = self.runtime.snapshot().state
        if state is RelaxationState.INACTIVE:
            self.runtime.enter_center()
        elif state is not RelaxationState.CENTER:
            return
        self.show()
        self.raise_()
        self.activateWindow()

    def close_to_chat(self) -> None:
        if self._closing_to_chat:
            return
        self._closing_to_chat = True
        try:
            state = self.runtime.snapshot().state
            if state is RelaxationState.CENTER:
                self.runtime.exit_to_conversation()
            if self.runtime.snapshot().state is RelaxationState.RETURNING:
                self.runtime.finalize_return()
            self.returned_to_chat.emit()
            self.close()
        finally:
            self._closing_to_chat = False

    def hide_for_content(self) -> None:
        """Hide the shell while leaving its Center runtime for a content handoff."""
        self.hide()

    def highlight_core_content(self, content_id: str | None) -> None:
        """Remember a user-owned core preference without starting playback."""
        content_id = {
            "muscle": "muscle_relaxation",
            "breathing": "breathing",
            "meditation": "meditation",
        }.get(content_id, content_id)
        if content_id not in self.core_buttons:
            self.preferred_core_content_id = None
            return
        self.preferred_core_content_id = content_id
        self.stack.setCurrentWidget(self.center_page)
        self.core_buttons[content_id].setFocus()

    def show_games_page(self) -> None:
        """Show the leisure selection page without starting a game."""
        self.stack.setCurrentWidget(self.games_page)

    def restore_after_core_failure(self, message: str = "内容暂时无法播放，可以换一个选项。") -> None:
        """Return to the core selection page after a provider failure."""
        if self.runtime.snapshot().state is not RelaxationState.CENTER:
            return
        self.center_status_label.setText(message)
        self.stack.setCurrentWidget(self.center_page)
        self.show()
        self.raise_()
        self.activateWindow()

    def restore_after_game(self, message: str = "") -> None:
        """Restore the Center Games page after a leisure run ends."""
        if self.runtime.snapshot().state is not RelaxationState.CENTER:
            return
        self.games_status_label.setText(message)
        self.show_games_page()
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        if not self._closing_to_chat:
            self.close_to_chat()
        event.accept()
