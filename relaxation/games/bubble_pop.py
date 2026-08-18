"""A quiet, native bubble interaction.

The motion and pointer hit-testing are adapted from the MIT-licensed bubble
mechanics reviewed from ``sausi-7/games`` at a pinned commit.  Presentation,
timing, colours, and the no-pressure interaction are implemented locally.
No score, countdown, lives, level, reward, asset, or game-over concept exists
in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import random
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


@dataclass
class Bubble:
    x: float
    y: float
    radius: float
    vx: float
    vy: float


class BubblePopModel:
    """Deterministic floating-bubble mechanics with a bounded object list."""

    def __init__(
        self,
        *,
        width: int = 640,
        height: int = 420,
        rng: random.Random | None = None,
        max_bubbles: int = 14,
    ) -> None:
        if width <= 0 or height <= 0 or max_bubbles <= 0:
            raise ValueError("bubble canvas and max_bubbles must be positive")
        self.width = float(width)
        self.height = float(height)
        self.rng = rng or random.Random()
        self.max_bubbles = int(max_bubbles)
        self.bubbles: list[Bubble] = []

    def spawn(
        self,
        *,
        x: float | None = None,
        y: float | None = None,
        radius: float | None = None,
        velocity: tuple[float, float] | None = None,
    ) -> Bubble | None:
        if len(self.bubbles) >= self.max_bubbles:
            return None
        r = float(radius if radius is not None else self.rng.uniform(16.0, 30.0))
        r = max(4.0, min(r, min(self.width, self.height) / 3.0))
        px = float(x if x is not None else self.rng.uniform(r, self.width - r))
        py = float(y if y is not None else self.height + r)
        vx, vy = velocity or (
            self.rng.uniform(-18.0, 18.0),
            self.rng.uniform(-54.0, -28.0),
        )
        bubble = Bubble(px, py, r, float(vx), float(vy))
        self.bubbles.append(bubble)
        return bubble

    def tick(self, dt_seconds: float) -> int:
        """Advance motion and return the number of bubbles leaving the canvas."""
        dt = max(0.0, float(dt_seconds))
        remaining: list[Bubble] = []
        removed = 0
        for bubble in self.bubbles:
            bubble.x += bubble.vx * dt
            bubble.y += bubble.vy * dt
            if bubble.x - bubble.radius < 0:
                bubble.x = bubble.radius
                bubble.vx = abs(bubble.vx)
            elif bubble.x + bubble.radius > self.width:
                bubble.x = self.width - bubble.radius
                bubble.vx = -abs(bubble.vx)
            if bubble.y + bubble.radius >= -1:
                remaining.append(bubble)
            else:
                removed += 1
        self.bubbles = remaining
        return removed

    def hit_test(self, x: float, y: float) -> Bubble | None:
        for bubble in reversed(self.bubbles):
            if (bubble.x - x) ** 2 + (bubble.y - y) ** 2 <= bubble.radius**2:
                return bubble
        return None

    def pop_at(self, x: float, y: float) -> bool:
        bubble = self.hit_test(x, y)
        if bubble is None:
            return False
        self.bubbles.remove(bubble)
        return True


class BubblePopWidget(QWidget):
    """Paint the model and provide optional pointer interaction."""

    quit_requested = Signal()

    def __init__(self, *, model: BubblePopModel | None = None, parent=None) -> None:
        super().__init__(parent)
        self.model = model or BubblePopModel()
        self._last_tick_ms = 0
        self._elapsed_ms = 0
        self.setMinimumSize(int(self.model.width), int(self.model.height))
        self.setMouseTracking(True)
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(33)
        self._animation_timer.timeout.connect(self._advance)
        self._animation_timer.start()

    def _advance(self) -> None:
        self._elapsed_ms += self._animation_timer.interval()
        self.model.tick(self._animation_timer.interval() / 1000.0)
        if self._elapsed_ms >= 700:
            self._elapsed_ms = 0
            self.model.spawn()
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton:
            self.model.pop_at(event.position().x(), event.position().y())
            self.update()
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#e8f5f2"))
        palette = ("#88c9c0", "#a6dcef", "#f7c8a3", "#c9b6e4", "#f5d491")
        for index, bubble in enumerate(self.model.bubbles):
            color = QColor(palette[index % len(palette)])
            color.setAlpha(165)
            painter.setBrush(color)
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.drawEllipse(
                int(bubble.x - bubble.radius),
                int(bubble.y - bubble.radius),
                int(bubble.radius * 2),
                int(bubble.radius * 2),
            )
        painter.end()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._animation_timer.stop()
        self.quit_requested.emit()
        super().closeEvent(event)
