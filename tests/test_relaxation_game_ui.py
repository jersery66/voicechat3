"""Native game host UI contracts."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not available")
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])

from ui.relaxation_games import RelaxationGameDialog  # noqa: E402


@pytest.mark.parametrize("content_id", ["bubble_pop", "gentle_search", "calm_puzzle", "falling_leaves"])
def test_game_dialog_uses_local_native_widget_and_has_explicit_exit(qapp, content_id):
    dialog = RelaxationGameDialog(content_id=content_id)
    assert dialog.content_id == content_id
    assert dialog.game_widget is not None
    assert dialog.exit_button.isEnabled() is True
    emitted = []
    dialog.game_finished.connect(emitted.append)
    dialog.exit_button.click()
    assert emitted == [False]
    dialog.deleteLater()
