"""Phase 3 catalog/runtime/provider orchestration contracts."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not available")
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import QObject, Signal  # noqa: E402

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
    window._relaxation_return_context = None
    window._engine_submit = lambda command: window.calls.append(("engine", command))
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


def test_available_games_are_not_started_without_a_center_selection():
    window = _window_stub()
    assert window.relaxation_catalog.require("bubble_pop").is_available is True
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


def test_direct_core_provider_failure_uses_provider_failed_engine_contract():
    window = _completion_window()
    window._active_relaxation_entry_source = "DIRECT_SHORTCUT"

    window._on_video_finished("breathing", completed=False)

    command = next(command for command in window._engine_events if command.kind == "relaxation_finished")
    assert command.provider_failed is True
    assert window.report_service.recorded == []


def test_center_core_provider_failure_returns_center_without_completed_relaxation():
    window = _completion_window()
    restored = []
    window._relaxation_center_dialog = SimpleNamespace(
        restore_after_core_failure=lambda: restored.append(True),
    )

    window._on_video_finished("breathing", completed=False)

    command = next(command for command in window._engine_events if command.kind == "relaxation_finished")
    assert command.provider_failed is True
    assert window.relaxation_runtime.snapshot().state is RelaxationState.CENTER
    assert restored == [True]
    assert window.report_service.recorded == []


def test_center_game_selection_starts_native_game_runtime_and_finishes_without_legacy_service(monkeypatch):
    import ui.relaxation_games as game_ui

    class FakeGameDialog(QObject):
        game_finished = Signal(bool)

        def __init__(self, *, content_id, parent=None):
            super().__init__()
            self.content_id = content_id
            self.shown = False

        def show(self):
            self.shown = True

        def close(self):
            pass

    monkeypatch.setattr(game_ui, "RelaxationGameDialog", FakeGameDialog)
    window = _window_stub()
    window._relaxation_game_dialog = None
    window.control_panel = SimpleNamespace(stop_all_blinks=lambda: None, set_status=lambda _text: None)
    window.chat_panel = SimpleNamespace(add_system_message=lambda _text: None)
    window.report_service = SimpleNamespace(activity_log=[], record_relaxation=lambda _name: None)

    assert window._start_relaxation_game("bubble_pop") is True
    assert window.relaxation_runtime.snapshot().state is RelaxationState.RUNNING
    assert window.relaxation_runtime.snapshot().selected_content_id == "bubble_pop"
    assert window._relaxation_game_dialog is None
    assert window._pending_leisure_game_start == "bubble_pop"

    window._handle_engine_event(SimpleNamespace(kind="leisure_started", content_id="bubble_pop"))
    dialog = window._relaxation_game_dialog
    assert dialog.content_id == "bubble_pop"
    assert dialog.shown is True

    dialog.game_finished.emit(False)
    assert window.relaxation_runtime.snapshot().state is RelaxationState.CENTER


def test_leisure_game_completion_records_usage_only_and_keeps_center_runtime():
    window = _window_stub()
    window.relaxation_runtime.enter_center()
    window.relaxation_runtime.start_content("bubble_pop")
    window._relaxation_game_dialog = SimpleNamespace(close=lambda: None)
    window.report_service = SimpleNamespace(activity_log=[])
    window.report_service.record_relaxation = lambda _name: (_ for _ in ()).throw(
        AssertionError("leisure must not be recorded as core relaxation")
    )
    window.chat_panel = SimpleNamespace(add_system_message=lambda text: setattr(window, "message", text))
    window.control_panel = SimpleNamespace(set_status=lambda text: setattr(window, "status", text))

    window._on_relaxation_game_finished("bubble_pop", completed=True)

    assert window.relaxation_runtime.snapshot().state is RelaxationState.CENTER
    assert window.report_service.activity_log[0]["type"] == "leisure"
    assert window.report_service.activity_log[0]["cancelled"] is False
    assert not hasattr(window, "message")
    assert not hasattr(window, "status")
