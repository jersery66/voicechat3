"""Layer-2 headless UI tests: real MainWindow on the offscreen Qt platform.

Requires a working PySide6 install (skipped otherwise).  Model loading is
monkeypatched below, so this test exercises the authoritative lifecycle
bridge without external model services.

IMPORTANT: MainWindow.__init__ unconditionally spawns the model-loading
thread; the fixture monkeypatches load_models to a no-op so these tests
never touch GPU/audio/Ollama on ANY machine (including deployment boxes
where the real models would otherwise load during test runs).
"""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not available")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.session_fsm import SessionState  # noqa: E402
from app.contracts import EndSessionCommand  # noqa: E402
from core.types import EndType  # noqa: E402

@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def wait_until(predicate, timeout=3.0):
    """Poll a predicate. Works because SessionEngine state transitions
    happen on its own worker thread, independent of the Qt event loop."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def window(qapp, monkeypatch):
    from ui.main_window import MainWindow
    # Never start the real model-loading chain in tests (it would load
    # FunASR/VoxCPM and warm up Ollama on deployment machines).
    monkeypatch.setattr(MainWindow, "load_models", lambda self: None)
    w = MainWindow()
    yield w
    engine = getattr(w, "session_engine", None)
    if engine:
        engine.shutdown(timeout=2)


class TestHeadlessBoot:
    def test_session_engine_created(self, window):
        assert window.session_engine is not None
        assert window.session_engine.state == SessionState.IDLE

    def test_relaxation_center_shell_opens_once_and_returns_cleanly(self, window):
        window._open_relaxation_center()
        dialog = window._relaxation_center_dialog
        assert dialog is not None
        assert dialog.isVisible()
        first_session = window.relaxation_runtime.snapshot().relaxation_session_id
        window._open_relaxation_center()
        assert window.relaxation_runtime.snapshot().relaxation_session_id == first_session
        dialog.close_to_chat()
        assert window.relaxation_runtime.snapshot().state.value == "INACTIVE"

    def test_production_window_has_no_legacy_crisis_ui(self, window):
        assert not hasattr(window, "_show_crisis_dialog")
        assert not hasattr(window, "safety_gate")

    def test_start_new_session_forwarded_to_engine(self, window):
        window.current_user_id = "HEADLESS-001"
        window._start_new_session()
        assert wait_until(
            lambda: window.session_engine.state == SessionState.CHATTING
        ), "engine did not reach CHATTING after _start_new_session"

    def test_end_session_forwarded_to_engine(self, window):
        window.current_user_id = "HEADLESS-002"
        window._start_new_session()
        assert wait_until(
            lambda: window.session_engine.state == SessionState.CHATTING
        )
        # explicit exit path: allow_force_relaxation=False goes straight to
        # SESSION_ENDING inside the engine (legacy QUIT parity)
        window._engine_submit(EndSessionCommand(
            end_type=EndType.QUIT,
            allow_force_relaxation=False,
            source="headless_test",
        ))
        assert wait_until(
            lambda: window.session_engine.state == SessionState.SESSION_ENDING
        ), "engine did not reach SESSION_ENDING after end command"

    def test_new_session_resets_engine(self, window):
        window.current_user_id = "HEADLESS-003"
        window._start_new_session()
        assert wait_until(
            lambda: window.session_engine.state == SessionState.CHATTING
        )
        window._engine_submit(EndSessionCommand(
            end_type=EndType.QUIT, allow_force_relaxation=False))
        assert wait_until(
            lambda: window.session_engine.state == SessionState.SESSION_ENDING
        )
        window._start_new_session()
        assert wait_until(
            lambda: window.session_engine.state == SessionState.CHATTING
        ), "engine did not reset to CHATTING for the new session"

    def test_failed_report_is_not_presented_as_saved(self, window):
        window._on_session_finished(report_ok=False)

        assert window._current_report_generated is False
        assert "失败" in window.control_panel.status_label.text()

    def test_failed_game_is_not_recorded_as_relaxation(self, window):
        class Report:
            activity_log = []

            def __init__(self):
                self.relaxations = []

            def record_relaxation(self, name):
                self.relaxations.append(name)

        report = Report()
        window.report_service = report
        window._on_game_finished(completed=False)

        assert report.relaxations == []
        assert "未完成" in window.control_panel.status_label.text()
