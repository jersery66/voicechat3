"""Completion and skip-dialog contracts for Untangle campaign flow."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not available")
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


from relaxation.puzzles.untangle.campaign import CampaignMode  # noqa: E402
from ui.untangle_preview import UntanglePreviewWindow  # noqa: E402


def _complete(window: UntanglePreviewWindow) -> None:
    for point in window.campaign.model.target_positions:
        window.campaign.model.move_point(point.id, point.x, point.y)
    window._on_completed()
    window._show_completion_dialog()


def test_level_complete_dialog_primary_next_loads_following_level(qapp):
    window = UntanglePreviewWindow(seed=7)
    _complete(window)
    dialog = window._completion_dialog
    assert dialog is not None
    assert dialog.next_button.text() == "下一关"
    assert dialog.next_button.isDefault() is True
    dialog.next_button.click()
    assert window.campaign.current_level.number == 2
    assert window._completion_dialog is None
    window.deleteLater()


def test_level_complete_dialog_replay_resets_without_losing_progress(qapp):
    window = UntanglePreviewWindow(seed=7)
    _complete(window)
    window._completion_dialog.replay_button.click()
    assert window.campaign.current_level.number == 1
    assert window.campaign.model.completed is False
    assert 1 in window.campaign.progress.completed_levels
    window.deleteLater()


def test_level_complete_dialog_selector_does_not_auto_start_a_different_level(qapp):
    window = UntanglePreviewWindow(seed=7)
    _complete(window)
    window._completion_dialog.select_button.click()
    assert window.campaign.current_level.number == 1
    assert window.level_combo.currentIndex() == 0
    window.deleteLater()


def test_final_level_uses_campaign_complete_dialog_and_endless_button(qapp):
    window = UntanglePreviewWindow(seed=7)
    window.campaign.progress.completed_levels.update(range(1, 15))
    assert window.campaign.load_level(15) is True
    window.puzzle_widget.set_model(window.campaign.model)
    _complete(window)
    dialog = window._completion_dialog
    assert dialog is not None
    assert dialog.continue_button.text() == "继续挑战"
    assert dialog.restart_button.text() == "从第1关重新开始"
    dialog.continue_button.click()
    assert window.campaign.mode is CampaignMode.ENDLESS
    assert window._completion_dialog is None
    window.deleteLater()


def test_duplicate_completion_signal_keeps_one_dialog(qapp):
    window = UntanglePreviewWindow(seed=7)
    _complete(window)
    first = window._completion_dialog
    window._on_completed()
    window._show_completion_dialog()
    assert window._completion_dialog is first
    window.deleteLater()


def test_skip_requires_confirmation_then_advances_without_selector(qapp):
    window = UntanglePreviewWindow(seed=7)
    assert window._skip() is True
    assert window.campaign.current_level.number == 1
    assert window._skip_dialog is not None
    window._skip_dialog.confirm_button.click()
    assert window.campaign.current_level.number == 2
    assert window._skip_dialog is None
    window.deleteLater()


def test_skipping_final_level_opens_campaign_complete_dialog(qapp):
    window = UntanglePreviewWindow(seed=7)
    window.campaign.progress.completed_levels.update(range(1, 15))
    assert window.campaign.load_level(15) is True
    window.puzzle_widget.set_model(window.campaign.model)
    window._skip()
    window._skip_dialog.confirm_button.click()
    window._show_completion_dialog()
    assert window._completion_dialog is not None
    assert window._completion_dialog.continue_button.text() == "继续挑战"
    window.deleteLater()
