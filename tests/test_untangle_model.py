"""Untangle model, history, and completion contracts."""

from __future__ import annotations

import pytest

from relaxation.puzzles.untangle.generator import Difficulty, generate_puzzle
from relaxation.puzzles.untangle.model import UntangleModel


def test_model_move_recomputes_crossings_and_supports_undo_reset():
    model = UntangleModel(difficulty=Difficulty.EASY, seed=12)
    initial = model.state
    point_id = next(point.id for point in initial.points)
    point = initial.target_positions[point_id]

    assert model.move_point(point_id, point.x, point.y) is True
    assert model.state.points[point_id] == point
    assert model.can_undo is True
    assert model.undo() is True
    assert model.state.points == initial.points
    assert model.reset() is True
    assert model.state.points == initial.points


def test_model_can_complete_by_applying_target_positions():
    model = UntangleModel(difficulty=Difficulty.EASY, seed=13)
    for point in model.state.target_positions:
        model.move_point(point.id, point.x, point.y)

    assert model.completed is True
    assert model.crossing_count == 0
    assert model.state.completed is True
    assert model.move_point(0, 0.1, 0.1) is False


def test_new_puzzle_keeps_difficulty_and_changes_seed():
    model = UntangleModel(difficulty=Difficulty.NORMAL, seed=5)
    old_seed = model.seed
    assert model.new_puzzle() is True
    assert model.difficulty is Difficulty.NORMAL
    assert model.seed != old_seed


def test_invalid_difficulty_is_rejected():
    with pytest.raises(ValueError):
        UntangleModel(difficulty="hard")


def test_generator_target_embedding_is_planar():
    puzzle = generate_puzzle(Difficulty.CHALLENGE, seed=99)
    assert puzzle.target_crossing_count == 0
    assert puzzle.initial_crossing_count >= 6


def test_drag_rejects_positions_that_overlap_another_point():
    model = UntangleModel(difficulty=Difficulty.EASY, seed=12)
    first, second = model.points[:2]
    before = model.state.points

    assert model.begin_drag(first.id) is True
    assert model.drag_point(first.id, second.x, second.y) is False
    assert model.end_drag() is False
    assert model.state.points == before
    assert model.can_undo is False


def test_drag_keeps_board_clamp_and_all_points_separated():
    model = UntangleModel(difficulty=Difficulty.NORMAL, seed=21)
    point = model.points[0]
    assert model.move_point(point.id, -10, 10) is True
    moved = model.points[point.id]
    assert 0.04 <= moved.x <= 0.96
    assert 0.04 <= moved.y <= 0.96
    for other in model.points:
        if other.id == point.id:
            continue
        distance = ((moved.x - other.x) ** 2 + (moved.y - other.y) ** 2) ** 0.5
        assert distance >= model.MIN_POINT_SEPARATION
