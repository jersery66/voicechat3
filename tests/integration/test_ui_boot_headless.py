"""Layer-2 headless UI tests: real MainWindow on the offscreen Qt platform.

Requires a working PySide6 install (skipped otherwise, e.g. on machines
where the Qt DLLs cannot load). Validates:
  - MainWindow constructs with the shadow SessionEngine enabled
  - lifecycle forwarding: _start_new_session -> engine CHATTING
  - _engine_submit(EndSessionCommand) -> engine SESSION_ENDING
  - engine shuts down cleanly

These tests never touch GPU/audio/network: services stay None (models are
not loaded), which is exactly the boot state of the real app.
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
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def window(qapp):
    from ui.main_window import MainWindow
    w = MainWindow()
    yield w
    engine = getattr(w, "session_engine", None)
    if engine:
        engine.shutdown(timeout=2)


class TestHeadlessBoot:
    def test_shadow_engine_created(self, window):
        assert window.session_engine is not None
        assert window.session_engine.state == SessionState.IDLE

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
