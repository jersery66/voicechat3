"""Phase 3 catalog/runtime/provider orchestration contracts."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not available")
from PySide6.QtWidgets import QApplication  # noqa: E402

from relaxation.catalog import build_default_catalog
from relaxation.contracts import RelaxationState
from relaxation.runtime import RelaxationRuntime
from ui.main_window import MainWindow
from ui.relaxation_center import RelaxationCenterDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window_stub():
    window = MainWindow.__new__(MainWindow)
    window.relaxation_catalog = build_default_catalog()
    window.relaxation_runtime = RelaxationRuntime(window.relaxation_catalog)
    window._relaxation_center_dialog = None
    window.calls = []
    window._engine_can_play_video = lambda: True
    window._play_relaxation_video = lambda content_id: window.calls.append(
        (content_id, window.relaxation_runtime.snapshot().state)
    )
    return window


def test_core_executor_starts_runtime_before_provider_call():
    window = _window_stub()

    assert window._start_core_relaxation("breathing", "DIRECT_SHORTCUT") is True
    assert window.calls == [("breathing", RelaxationState.RUNNING)]
    assert window.relaxation_runtime.snapshot().selected_content_id == "breathing"


def test_direct_and_center_core_entries_share_one_executor(qapp):
    window = _window_stub()
    window._start_core_relaxation = lambda content_id, entry_source: window.calls.append(
        (content_id, entry_source)
    ) or True
    dialog = RelaxationCenterDialog(
        catalog=window.relaxation_catalog,
        runtime=window.relaxation_runtime,
    )
    window._relaxation_center_dialog = dialog

    window._start_core_relaxation("breathing", "DIRECT_SHORTCUT")
    window._on_center_core_content("breathing")

    assert window.calls == [
        ("breathing", "DIRECT_SHORTCUT"),
        ("breathing", "CENTER"),
    ]
    dialog.deleteLater()


def test_center_core_card_disabled_when_catalog_content_unavailable(qapp):
    catalog = build_default_catalog()
    disabled = catalog.require("breathing").model_copy(update={"enabled": False})
    definitions = tuple(disabled if item.id == "breathing" else item for item in catalog)
    from relaxation.catalog import RelaxationCatalog

    dialog = RelaxationCenterDialog(
        catalog=RelaxationCatalog(definitions),
        runtime=RelaxationRuntime(RelaxationCatalog(definitions)),
    )
    assert dialog.core_buttons["breathing"].isEnabled() is False
    dialog.deleteLater()


def test_planned_games_do_not_enter_runtime():
    window = _window_stub()
    assert window.relaxation_catalog.require("bubble_pop").is_available is False
    assert window.relaxation_runtime.snapshot().state is RelaxationState.INACTIVE


def _completion_window():
    window = _window_stub()
    window.relaxation_runtime.enter_center()
    window.relaxation_runtime.start_content("breathing")
    window._engine_events = []
    window._engine_submit = lambda command: window._engine_events.append(command)
    window.report_service = SimpleNamespace(activity_log=[], recorded=[])
    window.report_service.record_relaxation = lambda name: window.report_service.recorded.append(name)
    window.pipeline = None
    window.chat_panel = SimpleNamespace(add_system_message=lambda _text: None)
    window.control_panel = SimpleNamespace(set_status=lambda _text: None)
    window._play_tts_async = lambda _text: None
    window._end_decision_open = False
    window._pre_end_relax_prompted = False
    window._active_relaxation_entry_source = "CENTER"
    return window


def test_successful_core_run_completes_runtime_and_records_once():
    window = _completion_window()

    window._on_video_finished("breathing", completed=True)
    assert window.relaxation_runtime.snapshot().state is RelaxationState.INACTIVE
    assert window.report_service.recorded == ["呼吸放松"]
    assert len(window.report_service.activity_log) == 1
    assert window.report_service.activity_log[0]["content_id"] == "breathing"

    window._on_video_finished("breathing", completed=True)
    assert window.report_service.recorded == ["呼吸放松"]
    assert len(window.report_service.activity_log) == 1


def test_failed_core_run_cancels_runtime_and_is_not_recorded():
    window = _completion_window()

    window._on_video_finished("breathing", completed=False)
    assert window.relaxation_runtime.snapshot().state is RelaxationState.INACTIVE
    assert window.report_service.recorded == []
    assert window.report_service.activity_log == []
