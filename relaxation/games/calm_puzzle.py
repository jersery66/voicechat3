"""A small, self-contained picture-free jigsaw board."""

from __future__ import annotations

from dataclasses import dataclass
import math
import random

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


@dataclass(frozen=True)
class PuzzlePiece:
    index: int
    slot: int


class CalmPuzzleModel:
    """Deterministic 4/6/9-piece placement model using local primitives."""

    ALLOWED_PIECE_COUNTS = (4, 6, 9)

    def __init__(self, *, piece_count: int = 4, rng: random.Random | None = None) -> None:
        if piece_count not in self.ALLOWED_PIECE_COUNTS:
            raise ValueError("piece_count must be 4, 6, or 9")
        self.piece_count = int(piece_count)
        self.rng = rng or random.Random()
        self.piece_at_slot: list[int] = list(range(self.piece_count))
        self._shuffle()

    @property
    def pieces(self) -> tuple[PuzzlePiece, ...]:
        return tuple(
            PuzzlePiece(index=piece, slot=slot)
            for slot, piece in enumerate(self.piece_at_slot)
        )

    @property
    def completed(self) -> bool:
        return all(piece == slot for slot, piece in enumerate(self.piece_at_slot))

    def _shuffle(self) -> None:
        self.rng.shuffle(self.piece_at_slot)
        if self.piece_count > 1 and self.completed:
            self.piece_at_slot[0], self.piece_at_slot[1] = self.piece_at_slot[1], self.piece_at_slot[0]

    def place_piece(self, piece_index: int, slot: int) -> bool:
        if not 0 <= piece_index < self.piece_count or not 0 <= slot < self.piece_count:
            return False
        current_slot = self.piece_at_slot.index(piece_index)
        other_piece = self.piece_at_slot[slot]
        self.piece_at_slot[slot] = piece_index
        self.piece_at_slot[current_slot] = other_piece
        return True


class CalmPuzzleWidget(QWidget):
    complete_requested = Signal()
    quit_requested = Signal()

    def __init__(self, *, model: CalmPuzzleModel | None = None, parent=None) -> None:
        super().__init__(parent)
        self.model = model or CalmPuzzleModel()
        self._selected_piece: int | None = None
        self.setMinimumSize(520, 420)

    def _grid_shape(self) -> tuple[int, int]:
        if self.model.piece_count == 6:
            return 2, 3
        side = int(math.sqrt(self.model.piece_count))
        return side, side

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        rows, columns = self._grid_shape()
        margin = 28.0
        board = min(self.width() - 2 * margin, self.height() - 2 * margin)
        cell_w, cell_h = board / columns, board / rows
        x = int((event.position().x() - margin) // cell_w)
        y = int((event.position().y() - margin) // cell_h)
        slot = y * columns + x
        if not 0 <= slot < self.model.piece_count:
            return super().mousePressEvent(event)
        piece = self.model.piece_at_slot[slot]
        if self._selected_piece is None:
            self._selected_piece = piece
        else:
            self.model.place_piece(self._selected_piece, slot)
            self._selected_piece = None
            if self.model.completed:
                self.complete_requested.emit()
        self.update()
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f7f1e7"))
        rows, columns = self._grid_shape()
        margin = 28.0
        board = min(self.width() - 2 * margin, self.height() - 2 * margin)
        cell_w, cell_h = board / columns, board / rows
        colours = ("#f2c6c2", "#c8d9f0", "#cbe7c5", "#f0dfb4", "#d9c7ea", "#c3e3e4", "#f2d0a7", "#d6dbb9", "#e4c8c8")
        for slot, piece in enumerate(self.model.piece_at_slot):
            row, column = divmod(slot, columns)
            rect = QRectF(margin + column * cell_w + 4, margin + row * cell_h + 4, cell_w - 8, cell_h - 8)
            painter.setBrush(QColor(colours[piece % len(colours)]))
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.drawRoundedRect(rect, 10, 10)
            painter.setPen(QColor("#5e554b"))
            painter.drawText(rect, Qt.AlignCenter, str(piece + 1))
        painter.end()
