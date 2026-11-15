"""Phase 4 native leisure-game contracts.

The tests exercise deterministic game mechanics directly.  The widgets are
only a thin PySide6 presentation layer; none of these tests require a media
engine, pygame, a browser runtime, or third-party game assets.
"""

from __future__ import annotations

import inspect
import random

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not available")
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])

from relaxation.games.bubble_pop import BubblePopModel, BubblePopWidget  # noqa: E402
from relaxation.games.calm_puzzle import CalmPuzzleModel  # noqa: E402
from relaxation.games.falling_leaves import FallingLeavesModel  # noqa: E402
from relaxation.games.gentle_search import GentleSearchModel  # noqa: E402


def test_bubble_pop_ports_only_calm_motion_and_pointer_hit_without_game_pressure():
    model = BubblePopModel(width=120, height=100, rng=random.Random(4), max_bubbles=2)
    bubble = model.spawn(x=40, y=90, radius=10, velocity=(0, -20))

    assert bubble is not None
    model.tick(0.5)
    assert bubble.y == pytest.approx(80.0)
    assert model.hit_test(40, 80) is bubble
    assert model.pop_at(40, 80) is True
    assert model.bubbles == []
    assert not any(
        name in vars(model)
        for name in ("score", "lives", "game_over", "level", "difficulty", "reward")
    )


def test_bubble_pop_spawn_is_bounded_and_widget_is_native_qt(qapp):
    model = BubblePopModel(width=120, height=100, rng=random.Random(1), max_bubbles=1)
    assert model.spawn() is not None
    assert model.spawn() is None
    widget = BubblePopWidget(model=model)
    assert widget.model is model
    assert "pygame" not in inspect.getsource(BubblePopWidget).lower()
    widget.deleteLater()


def test_gentle_search_has_one_subtle_target_and_no_persistent_score():
    model = GentleSearchModel(rng=random.Random(2), grid_size=4, trial_limit=6)
    assert len(model.cells) == 16
    assert sum(cell.is_target for cell in model.cells) == 1
    target = model.target_index
    assert model.click_cell((target + 1) % 16) == "keep_looking"
    assert model.click_cell(target) == "found"
    assert model.trials_completed == 1
    assert not any(name in vars(model) for name in ("score", "accuracy", "reaction_time"))


def test_gentle_search_ends_after_six_found_targets_without_penalty():
    model = GentleSearchModel(rng=random.Random(3), grid_size=4, trial_limit=6)
    outcomes = []
    for _ in range(6):
        outcomes.append(model.click_cell(model.target_index))
    assert outcomes[-1] == "complete"
    assert model.completed is True
    assert model.trials_completed == 6


@pytest.mark.parametrize("piece_count", [4, 6, 9])
def test_calm_puzzle_supports_small_deterministic_boards_without_timer_or_score(piece_count):
    model = CalmPuzzleModel(piece_count=piece_count, rng=random.Random(piece_count))
    assert len(model.pieces) == piece_count
    assert sorted(model.piece_at_slot) == list(range(piece_count))
    assert model.completed is False
    assert not any(name in vars(model) for name in ("score", "timer", "game_over"))

    for piece_index in range(piece_count):
        assert model.place_piece(piece_index, piece_index) is True
    assert model.completed is True


def test_falling_leaves_removes_missed_leaves_without_penalty_or_game_over():
    model = FallingLeavesModel(width=160, height=120, rng=random.Random(5), max_leaves=3)
    leaf = model.spawn(x=40, y=110, velocity=(0, 30))
    assert leaf is not None
    removed = model.tick(1.0)
    assert model.leaves == []
    assert removed == 1
    assert not any(name in vars(model) for name in ("score", "lives", "game_over", "difficulty"))


def test_falling_leaves_catcher_is_a_calm_optional_interaction():
    model = FallingLeavesModel(width=160, height=120, rng=random.Random(6), max_leaves=3)
    model.set_catcher_center(40)
    leaf = model.spawn(x=40, y=100, size=12, velocity=(0, 10))
    assert leaf is not None
    model.tick(0.5)
    assert model.catch_at(40) == 1
    assert model.leaves == []


def test_game_modules_do_not_import_browser_or_alternate_game_runtimes():
    import relaxation.games.bubble_pop as bubble_pop
    import relaxation.games.calm_puzzle as calm_puzzle
    import relaxation.games.falling_leaves as falling_leaves
    import relaxation.games.gentle_search as gentle_search

    for module in (bubble_pop, calm_puzzle, falling_leaves, gentle_search):
        source = inspect.getsource(module).lower()
        for forbidden in ("import pygame", "import phaser", "webview", "electron"):
            assert forbidden not in source
