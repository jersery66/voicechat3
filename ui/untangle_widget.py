"""Native PySide6 presentation for the standalone Untangle candidate."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from relaxation.puzzles.untangle.generator import Difficulty
from relaxation.puzzles.untangle.model import UntangleModel


class UntangleWidget(QWidget):
    completed_signal = Signal()

    def __init__(self, *, model: UntangleModel | None = None, parent=None) -> None:
        super().__init__(parent)
        self.model = model or UntangleModel()
        self._dragging_point: int | None = None
        self.status_label = QLabel(self)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: #766a5d; padding: 4px;")
        self.status_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.addWidget(self.status_label)
        self.setMinimumSize(620, 560)
        self._refresh_status()

    def set_model(self, model: UntangleModel) -> None:
        self.model = model
        self._dragging_point = None
        self._refresh_status()
        self.update()

    def undo(self) -> bool:
        changed = self.model.undo()
        self._refresh_status()
        self.update()
        return changed

    def reset(self) -> bool:
        changed = self.model.reset()
        self._refresh_status()
        self.update()
        return changed

    def new_puzzle(self, *, difficulty: Difficulty | str | None = None) -> bool:
        changed = self.model.new_puzzle(difficulty=difficulty)
        self._refresh_status()
        self.update()
        return changed

    def _board_rect(self):
        return self.rect().adjusted(18, 42, -18, -18)

    def _to_pixel(self, x: float, y: float) -> QPointF:
        board = self._board_rect()
        return QPointF(board.left() + x * board.width(), board.top() + y * board.height())

    def _to_normalized(self, position: QPointF) -> tuple[float, float]:
        board = self._board_rect()
        if board.width() <= 0 or board.height() <= 0:
            return 0.5, 0.5
        return (
            (position.x() - board.left()) / board.width(),
            (position.y() - board.top()) / board.height(),
        )

    def _refresh_status(self) -> None:
        if self.model.completed:
            self.status_label.setText("解开了。")
        else:
            self.status_label.setText(f"还有 {self.model.crossing_count} 处交叉")

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton and not self.model.completed:
            x, y = self._to_normalized(event.position())
            self._dragging_point = self.model.hit_test(x, y)
            if self._dragging_point is not None:
                self.model.begin_drag(self._dragging_point)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._dragging_point is not None:
            x, y = self._to_normalized(event.position())
            self.model.drag_point(self._dragging_point, x, y)
            self._refresh_status()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton and self._dragging_point is not None:
            self.model.end_drag()
            self._dragging_point = None
            self._refresh_status()
            self.update()
            if self.model.completed:
                self.completed_signal.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f4f0e8"))
        board = self._board_rect()
        painter.setPen(QPen(QColor("#ddd2c2"), 1))
        painter.setBrush(QColor("#fbf8f1"))
        painter.drawRoundedRect(board, 12, 12)

        points = {point.id: self._to_pixel(point.x, point.y) for point in self.model.points}
        for index, edge in enumerate(self.model.edges):
            crossing = index in self.model.crossing_edges
            painter.setPen(QPen(QColor("#d39a78" if crossing else "#87979b"), 3 if crossing else 2))
            painter.drawLine(points[edge.a], points[edge.b])

        for point in self.model.points:
            position = points[point.id]
            if point.id == self._dragging_point:
                painter.setBrush(QColor(255, 229, 166, 150))
                painter.setPen(QPen(QColor("#d6a75b"), 3))
                painter.drawEllipse(position, 18, 18)
            painter.setBrush(QColor("#84aeb0"))
            painter.setPen(QPen(QColor("#527d80"), 2))
            painter.drawEllipse(position, 11, 11)
        painter.end()
