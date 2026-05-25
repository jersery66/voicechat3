"""Tests for the session-end guard controller."""

from services.session_end_controller import SessionEndController


class TestSessionEndController:
    def test_begin_rejects_duplicate(self):
        controller = SessionEndController()

        first = controller.begin()
        second = controller.begin()

        assert first.accepted is True
        assert second.accepted is False
        assert second.reason == "already_ending"

    def test_defer_for_relaxation_releases_guard(self):
        controller = SessionEndController()

        assert controller.begin().accepted is True
        controller.defer_for_relaxation()

        assert controller.begin().accepted is True

    def test_reset_releases_guard(self):
        controller = SessionEndController()
        controller.begin()

        controller.reset()

        assert controller.begin().accepted is True
