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
