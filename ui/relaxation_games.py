"""Native game host for the Relaxation Center.

This dialog is a presentation adapter only.  It does not make a
recommendation, write session state, score a participant, or invoke a model.
The owning window decides how the surrounding RelaxationRuntime is completed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from relaxation.games.bubble_pop import BubblePopWidget
from relaxation.games.calm_puzzle import CalmPuzzleWidget
from relaxation.games.falling_leaves import FallingLeavesWidget
from relaxation.games.gentle_search import GentleSearchWidget


_WIDGETS = {
    "bubble_pop": BubblePopWidget,
    "gentle_search": GentleSearchWidget,
    "calm_puzzle": CalmPuzzleWidget,
    "falling_leaves": FallingLeavesWidget,
}


class RelaxationGameDialog(QDialog):
    """Host one local game and expose one explicit participant exit path."""

    game_finished = Signal(bool)

    def __init__(self, *, content_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if content_id not in _WIDGETS:
            raise ValueError(f"unknown relaxation game: {content_id}")
        self.content_id = content_id
        self._result_emitted = False
        self.setWindowTitle("轻松玩一会儿")
        self.setModal(False)
        self.setMinimumSize(600, 520)

        root = QVBoxLayout(self)
        title = QLabel(
            {
                "bubble_pop": "泡泡",
                "gentle_search": "找一找",
                "calm_puzzle": "轻拼图",
                "falling_leaves": "接住落叶",
            }[content_id],
            self,
        )
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)
        self.game_widget = _WIDGETS[content_id](parent=self)
        root.addWidget(self.game_widget, 1)
        self.exit_button = QPushButton("结束练习", self)
        self.exit_button.clicked.connect(lambda: self.finish(False))
        root.addWidget(self.exit_button)

        complete_signal = getattr(self.game_widget, "complete_requested", None)
        if complete_signal is not None:
            complete_signal.connect(lambda: self.finish(True))
        quit_signal = getattr(self.game_widget, "quit_requested", None)
        if quit_signal is not None:
            quit_signal.connect(lambda: self.finish(False))

    def finish(self, completed: bool) -> None:
        if self._result_emitted:
            return
        self._result_emitted = True
        self.game_finished.emit(bool(completed))
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self._result_emitted:
            self._result_emitted = True
            self.game_finished.emit(False)
        event.accept()
