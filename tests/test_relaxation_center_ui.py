"""Phase 2 Relaxation Center UI shell contracts."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not available")

from PySide6.QtWidgets import QApplication  # noqa: E402

from relaxation.catalog import build_default_catalog  # noqa: E402
from relaxation.contracts import RelaxationState  # noqa: E402
from relaxation.runtime import RelaxationRuntime  # noqa: E402
from ui.control_panel import ControlPanel  # noqa: E402
from ui.relaxation_center import RelaxationCenterDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def catalog():
    return build_default_catalog()


def test_control_panel_keeps_core_buttons_and_demotes_legacy_peers(qapp):
    panel = ControlPanel()

    assert panel.btn_breathing.isVisible() is False or panel.btn_breathing.isEnabled() is True
    assert panel.btn_muscle.isVisible() is False or panel.btn_muscle.isEnabled() is True
    assert panel.btn_meditation.isVisible() is False or panel.btn_meditation.isEnabled() is True
    assert panel.btn_game.isVisible() is False
    assert panel.btn_media.isVisible() is False
    assert panel.btn_relaxation_center.text() == "轻松一下"
    assert hasattr(panel, "open_relaxation_center")


def test_center_reads_core_and_leisure_roles_from_catalog(qapp, catalog):
    runtime = RelaxationRuntime(catalog)
    dialog = RelaxationCenterDialog(catalog=catalog, runtime=runtime)

    assert set(dialog.core_content_ids) == {"breathing", "muscle_relaxation", "meditation"}
    assert set(dialog.leisure_game_ids) == {
        "bubble_pop", "gentle_search", "calm_puzzle", "falling_leaves"
    }
    assert dialog.core_buttons["breathing"].isEnabled() is True
    assert dialog.core_buttons["muscle_relaxation"].isEnabled() is True
    assert dialog.core_buttons["meditation"].isEnabled() is True
    assert dialog.games_button.isEnabled() is True
    assert dialog.videos_button.isEnabled() is False
    dialog.deleteLater()


def test_available_games_are_selectable_and_emit_content_request(qapp, catalog):
    runtime = RelaxationRuntime(catalog)
    dialog = RelaxationCenterDialog(catalog=catalog, runtime=runtime)
    requested = []
    dialog.game_content_requested.connect(requested.append)
    dialog.open_center()
    dialog.games_button.click()

    assert dialog.games_page.isVisible() is True
    for button in dialog.game_buttons.values():
        assert button.isEnabled() is True
        assert "即将开放" not in button.text()
    dialog.game_buttons["bubble_pop"].click()
    assert requested == ["bubble_pop"]
    assert runtime.snapshot().state is RelaxationState.CENTER
    dialog.close_to_chat()
    assert runtime.snapshot().state is RelaxationState.INACTIVE


def test_center_open_is_idempotent_and_can_return_to_chat(qapp, catalog):
    runtime = RelaxationRuntime(catalog)
    dialog = RelaxationCenterDialog(catalog=catalog, runtime=runtime)
    dialog.open_center()
    first_session = runtime.snapshot().relaxation_session_id
    dialog.open_center()
    assert runtime.snapshot().relaxation_session_id == first_session

    dialog.close_to_chat()
    assert runtime.snapshot().state is RelaxationState.INACTIVE


def test_games_page_returns_to_center(qapp, catalog):
    dialog = RelaxationCenterDialog(catalog=catalog, runtime=RelaxationRuntime(catalog))
    dialog.open_center()
    dialog.games_button.click()
    assert dialog.stack.currentWidget() is dialog.games_page
    dialog.games_back_button.click()
    assert dialog.stack.currentWidget() is dialog.center_page
    dialog.close_to_chat()


def test_center_can_be_restored_to_games_page_after_leisure_game(qapp, catalog):
    dialog = RelaxationCenterDialog(catalog=catalog, runtime=RelaxationRuntime(catalog))
    dialog.open_center()
    dialog.restore_after_game("这一小段结束了。")
    assert dialog.isVisible() is True
    assert dialog.stack.currentWidget() is dialog.games_page
    assert dialog.games_status_label.text() == "这一小段结束了。"
    dialog.close_to_chat()


def test_center_maps_canonical_muscle_preference_to_catalog_content(qapp, catalog):
    dialog = RelaxationCenterDialog(catalog=catalog, runtime=RelaxationRuntime(catalog))
    dialog.open_center()
    dialog.highlight_core_content("muscle")
    assert dialog.preferred_core_content_id == "muscle_relaxation"
    dialog.close_to_chat()


def test_center_shell_has_no_business_authority_imports():
    source = Path("ui/relaxation_center.py").read_text(encoding="utf-8")
    for forbidden in ("AgentService", "TurnPolicy", "ScaleRuntime", "SessionEngine"):
        assert forbidden not in source
