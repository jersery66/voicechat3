"""Headless native Untangle preview contracts."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not available")
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])

from ui.untangle_preview import UntanglePreviewWindow  # noqa: E402


def test_preview_boots_with_three_difficulties_and_controls(qapp):
    window = UntanglePreviewWindow(seed=7)
    assert window.title_label.text() == "解开线团"
    assert window.difficulty_combo.count() == 3
    assert window.undo_button.text() == "撤销"
    assert window.reset_button.text() == "重新开始"
    assert window.new_button.text() == "换一局"
    assert window.puzzle_widget.model.difficulty.value == "easy"
    window.deleteLater()


def test_preview_has_no_v1_runtime_or_forbidden_game_dependency_imports():
    source = Path("ui/untangle_preview.py").read_text(encoding="utf-8")
    widget_source = Path("ui/untangle_widget.py").read_text(encoding="utf-8")
    for forbidden in ("AgentService", "TurnPolicy", "ScaleRuntime", "SessionEngine", "RelaxationRuntime", "pygame", "webview", "phaser"):
        assert forbidden not in source
        assert forbidden not in widget_source
