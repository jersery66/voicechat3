"""Seeded generator corpus contracts for Untangle."""

from __future__ import annotations

import pytest

from relaxation.puzzles.untangle.generator import Difficulty, generate_puzzle


@pytest.mark.parametrize("difficulty,minimum_crossings", [
    (Difficulty.EASY, 2),
    (Difficulty.NORMAL, 4),
    (Difficulty.CHALLENGE, 6),
])
def test_generator_is_deterministic_valid_and_nontrivial(difficulty, minimum_crossings):
    for seed in range(30):
        first = generate_puzzle(difficulty, seed=seed)
        second = generate_puzzle(difficulty, seed=seed)
        assert first == second
        assert first.completed is False
        assert first.initial_crossing_count >= minimum_crossings
        assert first.crossing_count == first.initial_crossing_count
        assert first.target_crossing_count == 0
        assert len(first.points) == difficulty.point_count
        assert all(edge.a != edge.b for edge in first.edges)
        assert len(first.edges) == len({tuple(sorted((edge.a, edge.b))) for edge in first.edges})
        assert all(0.0 <= point.x <= 1.0 and 0.0 <= point.y <= 1.0 for point in first.points)
        degrees = [0] * len(first.points)
        for edge in first.edges:
            degrees[edge.a] += 1
            degrees[edge.b] += 1
        assert all(degree > 0 for degree in degrees)


def test_different_seeds_change_the_scrambled_puzzle():
    first = generate_puzzle(Difficulty.NORMAL, seed=1)
    second = generate_puzzle(Difficulty.NORMAL, seed=2)
    assert first.points != second.points or first.edges != second.edges


def test_difficulty_presets_are_six_ten_and_fifteen_points():
    assert Difficulty.EASY.point_count == 6
    assert Difficulty.NORMAL.point_count == 10
    assert Difficulty.CHALLENGE.point_count == 15


def _topology_signature(puzzle):
    adjacency = {point.id: set() for point in puzzle.target_positions}
    for edge in puzzle.edges:
        adjacency[edge.a].add(edge.b)
        adjacency[edge.b].add(edge.a)
    return tuple(tuple(sorted(adjacency[index])) for index in sorted(adjacency))


@pytest.mark.parametrize(
    "difficulty,minimum_signatures",
    [
        (Difficulty.EASY, 3),
        (Difficulty.NORMAL, 8),
        (Difficulty.CHALLENGE, 12),
    ],
)
def test_topology_diversity_corpus(difficulty, minimum_signatures):
    signatures = {_topology_signature(generate_puzzle(difficulty, seed=seed)) for seed in range(200)}
    assert len(signatures) >= minimum_signatures
