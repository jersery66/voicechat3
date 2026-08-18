"""Standalone human-playtest preview for V2-A Untangle."""

from __future__ import annotations

import argparse
import random
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMainWindow,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from relaxation.puzzles.untangle.generator import Difficulty
from relaxation.puzzles.untangle.model import UntangleModel
from ui.untangle_widget import UntangleWidget


class UntanglePreviewWindow(QMainWindow):
    def __init__(self, *, seed: int | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("解开线团")
        self.setMinimumSize(700, 700)
        self._seed_rng = random.Random(seed)
        self._seed = seed
        self.title_label = QLabel("解开线团")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #4f625f;")
        subtitle = QLabel("拖动圆点，让所有连线不再交叉。")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #766a5d; padding-bottom: 4px;")

        self.difficulty_combo = QComboBox()
        self.difficulty_combo.addItem("轻松 · 6 个点", Difficulty.EASY)
        self.difficulty_combo.addItem("标准 · 10 个点", Difficulty.NORMAL)
        self.difficulty_combo.addItem("挑战 · 15 个点", Difficulty.CHALLENGE)
        self.difficulty_combo.currentIndexChanged.connect(self._difficulty_changed)

        self.puzzle_widget = UntangleWidget(
            model=UntangleModel(difficulty=Difficulty.EASY, seed=seed)
        )
        self.puzzle_widget.completed_signal.connect(self._on_completed)

        self.undo_button = QPushButton("撤销")
        self.reset_button = QPushButton("重新开始")
        self.new_button = QPushButton("换一局")
        self.close_button = QPushButton("关闭")
        self.undo_button.clicked.connect(self.puzzle_widget.undo)
        self.reset_button.clicked.connect(self.puzzle_widget.reset)
        self.new_button.clicked.connect(self._new_puzzle)
        self.close_button.clicked.connect(self.close)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.puzzle_widget.undo)

        controls = QHBoxLayout()
        controls.addWidget(self.difficulty_combo)
        controls.addWidget(self.undo_button)
        controls.addWidget(self.reset_button)
        controls.addWidget(self.new_button)
        controls.addWidget(self.close_button)
        root = QVBoxLayout()
        root.addWidget(self.title_label)
        root.addWidget(subtitle)
        root.addLayout(controls)
        root.addWidget(self.puzzle_widget, 1)
        central = QWidget(self)
        central.setLayout(root)
        self.setCentralWidget(central)

    def _difficulty_changed(self, index: int) -> None:
        self.puzzle_widget.new_puzzle(difficulty=self.difficulty_combo.itemData(index))

    def _new_puzzle(self) -> None:
        self.puzzle_widget.new_puzzle(seed=self._seed_rng.randrange(2**31))

    def _on_completed(self) -> None:
        self.undo_button.setEnabled(False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv)
    window = UntanglePreviewWindow(seed=args.seed)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
