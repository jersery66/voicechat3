"""Native odd-cell visual search with no scoring or penalty."""

from __future__ import annotations

from dataclasses import dataclass
import random

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


@dataclass(frozen=True)
class SearchCell:
    index: int
    is_target: bool


class GentleSearchModel:
    """A bounded sequence of one-subtle-difference visual trials."""

    def __init__(
        self,
        *,
        rng: random.Random | None = None,
        grid_size: int = 4,
        trial_limit: int = 6,
    ) -> None:
        if grid_size < 2:
            raise ValueError("grid_size must be at least 2")
        if not 5 <= trial_limit <= 8:
            raise ValueError("trial_limit must be between 5 and 8")
        self.rng = rng or random.Random()
        self.grid_size = int(grid_size)
        self.trial_limit = int(trial_limit)
        self.trials_completed = 0
        self.completed = False
        self.cells: tuple[SearchCell, ...] = ()
        self.target_index = 0
        self._new_trial()

    def _new_trial(self) -> None:
        total = self.grid_size * self.grid_size
        self.target_index = self.rng.randrange(total)
        self.cells = tuple(
            SearchCell(index, index == self.target_index) for index in range(total)
        )

    def click_cell(self, index: int) -> str:
        if self.completed:
            return "complete"
        if index != self.target_index:
            return "keep_looking"
        self.trials_completed += 1
        if self.trials_completed >= self.trial_limit:
            self.completed = True
            return "complete"
        self._new_trial()
        return "found"


class GentleSearchWidget(QWidget):
    complete_requested = Signal()
    quit_requested = Signal()

    def __init__(self, *, model: GentleSearchModel | None = None, parent=None) -> None:
        super().__init__(parent)
        self.model = model or GentleSearchModel()
        self.setMinimumSize(520, 420)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.LeftButton or self.model.completed:
            return super().mousePressEvent(event)
        margin = 28.0
        board = min(self.width(), self.height() - 42) - 2 * margin
        cell = board / self.model.grid_size
        x = event.position().x() - margin
        y = event.position().y() - margin
        if x < 0 or y < 0 or x >= board or y >= board:
            return super().mousePressEvent(event)
        index = int(y // cell) * self.model.grid_size + int(x // cell)
        if self.model.click_cell(index) == "complete":
            self.complete_requested.emit()
        self.update()
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f5f1e9"))
        margin = 28.0
        board = min(self.width(), self.height() - 42) - 2 * margin
        cell = board / self.model.grid_size
        for item in self.model.cells:
            row, column = divmod(item.index, self.model.grid_size)
            rect = QRectF(margin + column * cell + 4, margin + row * cell + 4, cell - 8, cell - 8)
            painter.setBrush(QColor("#d9ece6" if not item.is_target else "#d4e8e6"))
            painter.setPen(QPen(QColor("#b9d8d0"), 1))
            painter.drawRoundedRect(rect, 10, 10)
        painter.end()
