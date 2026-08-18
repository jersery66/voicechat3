"""A non-competitive falling-leaves interaction."""

from __future__ import annotations

from dataclasses import dataclass
import random

from PySide6.QtCore import QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget


@dataclass
class Leaf:
    x: float
    y: float
    size: float
    vx: float
    vy: float


class FallingLeavesModel:
    """Leaves drift down; missed leaves simply leave the scene."""

    def __init__(
        self,
        *,
        width: int = 640,
        height: int = 420,
        rng: random.Random | None = None,
        max_leaves: int = 12,
    ) -> None:
        if width <= 0 or height <= 0 or max_leaves <= 0:
            raise ValueError("leaf canvas and max_leaves must be positive")
        self.width = float(width)
        self.height = float(height)
        self.rng = rng or random.Random()
        self.max_leaves = int(max_leaves)
        self.leaves: list[Leaf] = []
        self.catcher_center = self.width / 2
        self.catcher_width = min(120.0, self.width * 0.35)
        self.catcher_y = self.height - 34.0

    def spawn(
        self,
        *,
        x: float | None = None,
        y: float = 0.0,
        size: float | None = None,
        velocity: tuple[float, float] | None = None,
    ) -> Leaf | None:
        if len(self.leaves) >= self.max_leaves:
            return None
        s = max(6.0, float(size if size is not None else self.rng.uniform(10.0, 20.0)))
        px = float(x if x is not None else self.rng.uniform(s, self.width - s))
        vx, vy = velocity or (self.rng.uniform(-12.0, 12.0), self.rng.uniform(28.0, 54.0))
        leaf = Leaf(px, float(y), s, float(vx), float(vy))
        self.leaves.append(leaf)
        return leaf

    def set_catcher_center(self, x: float) -> None:
        half = self.catcher_width / 2
        self.catcher_center = max(half, min(float(x), self.width - half))

    def tick(self, dt_seconds: float) -> int:
        dt = max(0.0, float(dt_seconds))
        remaining: list[Leaf] = []
        removed = 0
        for leaf in self.leaves:
            leaf.x += leaf.vx * dt
            leaf.y += leaf.vy * dt
            if leaf.y - leaf.size > self.height:
                removed += 1
            else:
                remaining.append(leaf)
        self.leaves = remaining
        return removed

    def catch_at(self, x: float | None = None) -> int:
        if x is not None:
            self.set_catcher_center(x)
        left = self.catcher_center - self.catcher_width / 2
        right = self.catcher_center + self.catcher_width / 2
        caught = [leaf for leaf in self.leaves if left <= leaf.x <= right and leaf.y >= self.catcher_y - 40]
        for leaf in caught:
            self.leaves.remove(leaf)
        return len(caught)


class FallingLeavesWidget(QWidget):
    quit_requested = Signal()

    def __init__(self, *, model: FallingLeavesModel | None = None, parent=None) -> None:
        super().__init__(parent)
        self.model = model or FallingLeavesModel()
        self._elapsed_ms = 0
        self.setMinimumSize(int(self.model.width), int(self.model.height))
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(33)
        self._animation_timer.timeout.connect(self._advance)
        self._animation_timer.start()

    def _advance(self) -> None:
        self._elapsed_ms += self._animation_timer.interval()
        self.model.tick(self._animation_timer.interval() / 1000.0)
        if self._elapsed_ms >= 650:
            self._elapsed_ms = 0
            self.model.spawn()
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.model.set_catcher_center(event.position().x())
        self.model.catch_at()
        self.update()
        super().mouseMoveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#edf4ef"))
        for index, leaf in enumerate(self.model.leaves):
            painter.setBrush(QColor(("#e7b36a", "#d88663", "#c9a45c")[index % 3]))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QRectF(leaf.x - leaf.size / 2, leaf.y - leaf.size / 2, leaf.size, leaf.size * 0.72))
        painter.setBrush(QColor("#9bb7a1"))
        painter.drawRoundedRect(
            QRectF(
                self.model.catcher_center - self.model.catcher_width / 2,
                self.model.catcher_y,
                self.model.catcher_width,
                16,
            ),
            8,
            8,
        )
        painter.end()
