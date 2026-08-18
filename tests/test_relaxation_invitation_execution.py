"""Phase 5 invitation and Center-entry execution contracts."""

from __future__ import annotations

import queue
from types import SimpleNamespace

from conversation.contracts import TurnAction, TurnDecision
from ui.main_window import MainWindow


def _window_stub():
    window = MainWindow.__new__(MainWindow)
    window.processing_queue = queue.Queue()
    window.engine_commands = []
    window._pending_relaxation_preference = None
    window.is_recording = False
    window.stt_service = None
    window._engine_submit = lambda command: window.engine_commands.append(command)
    window.control_panel = SimpleNamespace(
        highlight_relax_button=lambda key: setattr(window, "highlighted", key),
        set_status=lambda text: setattr(window, "status", text),
    )
    return window


def test_proactive_relaxation_only_surfaces_center_invitation_and_never_starts_content():
    window = _window_stub()
    window._start_core_relaxation = lambda *_args: (_ for _ in ()).throw(
        AssertionError("invitation must not start a core item")
    )
    window._post_pipeline_routing(SimpleNamespace(
        turn_decision=TurnDecision(
            action=TurnAction.RECOMMEND_RELAXATION,
            intervention_type=None,
            reason="proactive_relaxation_accepted",
        ),
        scale_active=False,
    ))

    message = window.processing_queue.get_nowait()
    assert message == ("relaxation_invitation", None)
    assert not any(command.__class__.__name__ == "PlayRelaxationCommand" for command in window.engine_commands)


def test_explicit_core_preference_is_only_a_center_highlight_preference():
    window = _window_stub()
    window._post_pipeline_routing(SimpleNamespace(
        turn_decision=TurnDecision(
            action=TurnAction.RECOMMEND_RELAXATION,
            intervention_type="muscle",
            reason="user_relaxation_request",
        ),
        scale_active=False,
    ))

    assert window.processing_queue.get_nowait() == ("relaxation_invitation", "muscle")
    assert not any(command.__class__.__name__ == "PlayRelaxationCommand" for command in window.engine_commands)


def test_explicit_game_request_queues_games_center_entry_not_legacy_game_service():
    window = _window_stub()
    window._post_pipeline_routing(SimpleNamespace(
        turn_decision=TurnDecision(
            action=TurnAction.RECOMMEND_GAME,
            intervention_type=None,
            reason="user_game_request",
        ),
        scale_active=False,
    ))

    assert window.processing_queue.get_nowait() == ("open_games_center", None)
    assert not any(command.__class__.__name__ == "PlayGameCommand" for command in window.engine_commands)


def test_queue_entry_for_games_center_is_user_selection_boundary():
    window = _window_stub()
    calls = []
    window._open_relaxation_center = lambda *, show_games=False: calls.append(show_games)
    window.process_queue()

    # No unrelated task should be consumed before the explicit entry event.
    window.processing_queue.put(("open_games_center", None))
    window.process_queue()
    assert calls == [True]
