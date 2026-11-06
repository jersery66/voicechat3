"""End-session relaxation: choosing it starts the training, and it ends the session."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from ui import main_window as mw

ROOT = Path(__file__).resolve().parents[1]


def _app():
    return QApplication.instance() or QApplication([])


def _window():
    _app()  # QTimer.singleShot needs an application instance up front
    window = mw.MainWindow.__new__(mw.MainWindow)
    window._cancel_active_pipeline = lambda reason="": None
    window._get_end_relaxation_tag = lambda: "breathing"
    window.relaxation_catalog = SimpleNamespace(
        get=lambda cid: SimpleNamespace(display_name="呼吸放松训练")
    )
    window.chat_panel = SimpleNamespace(
        add_system_message=lambda *a, **k: None,
        clear_chat=lambda: None,
    )
    window.control_panel = SimpleNamespace(
        set_status=lambda *a, **k: None,
        highlight_relax_button=lambda *a, **k: None,
    )
    window._auto_ending_after_relaxation = False
    return window


def _pump(ms=260):
    app = _app()
    deadline = time.time() + ms / 1000.0
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def test_choosing_relaxation_starts_the_training_without_a_second_click():
    window = _window()
    started = []
    window._start_core_relaxation = lambda cid, entry_source=None: started.append(
        (cid, entry_source)
    ) or True

    window._end_session_with_relaxation()
    assert window._auto_ending_after_relaxation is True
    _pump()

    assert started == [("breathing", "END_FLOW")]


def test_a_failed_start_falls_back_to_the_manual_nudge():
    window = _window()
    window._start_core_relaxation = lambda cid, entry_source=None: False
    highlighted = []
    window.control_panel.highlight_relax_button = lambda tag: highlighted.append(tag)

    window._end_session_with_relaxation()
    _pump()

    # The session must not auto-end when the exercise never played.
    assert window._auto_ending_after_relaxation is False
    _pump()
    assert highlighted == ["breathing"]


def test_video_completion_ends_the_session_instead_of_resuming_chat():
    source = (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    body = source.split("def _on_video_finished", 1)[1].split("def _play_game", 1)[0]
    assert "_auto_ending_after_relaxation" in body
    assert 'source="auto_end_after_relaxation"' in body
    # The end-of-session path must not be swallowed by the "继续对话" branch.
    assert body.index("_auto_ending_after_relaxation") < body.index("继续对话中")
