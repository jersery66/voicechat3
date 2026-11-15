"""Standalone V2-A.2 campaign preview contracts."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not available")
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


from ui.untangle_preview import UntanglePreviewWindow  # noqa: E402


def test_preview_starts_campaign_with_fifteen_level_selector(qapp):
    window = UntanglePreviewWindow(seed=7)
    assert window.mode_label.text() == "闯关模式"
    assert window.level_combo.count() == 15
    assert window.level_label.text() == "第 1 / 15 关"
    assert window.chapter_label.text() == "第一章 · 理清头绪"
    assert window.hint_button.text() == "提示"
    assert window.skip_button.text() == "跳过"
    assert window.endless_button.text() == "自由挑战"
    window.deleteLater()


def test_preview_hint_and_endless_mode_are_local_controls(qapp):
    window = UntanglePreviewWindow(seed=7)
    assert window._hint() is True
    assert window.puzzle_widget.model.hint_level == 1
    assert window._enter_endless() is True
    assert window.mode_label.text() == "自由挑战"
    assert window.puzzle_widget.model.fixed_node_ids == frozenset()
    assert window._enter_campaign() is True
    assert window.mode_label.text() == "闯关模式"
    window.deleteLater()


def test_preview_exposes_campaign_completion_actions(qapp):
    window = UntanglePreviewWindow(seed=7)
    assert window.campaign.load_level(15) is True
    window.campaign.progress.completed_levels.update(range(1, 15))
    window.puzzle_widget.set_model(window.campaign.model)
    for point in window.campaign.model.target_positions:
        window.campaign.model.move_point(point.id, point.x, point.y)
    window._on_completed()
    assert "全部解开了" in window.completion_label.text()
    assert window.continue_button.text() == "继续挑战"
    assert window.continue_button.isHidden() is False
    window.deleteLater()
