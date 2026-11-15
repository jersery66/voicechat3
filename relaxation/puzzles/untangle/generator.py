"""Seeded planar graph generation for the Untangle candidate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import random
from typing import Iterable

from .geometry import Point, Segment, crossing_pair_count, edge_pair_crosses


class Difficulty(str, Enum):
    EASY = "easy"
    NORMAL = "normal"
    CHALLENGE = "challenge"

    @property
    def point_count(self) -> int:
        return {self.EASY: 6, self.NORMAL: 10, self.CHALLENGE: 15}[self]

    @property
    def minimum_crossings(self) -> int:
        return {self.EASY: 2, self.NORMAL: 4, self.CHALLENGE: 6}[self]


@dataclass(frozen=True)
class GeneratedPoint:
    id: int
    x: float
    y: float


@dataclass(frozen=True)
class GeneratedEdge:
    a: int
    b: int


@dataclass(frozen=True)
class GeneratedPuzzle:
    points: tuple[GeneratedPoint, ...]
    target_positions: tuple[GeneratedPoint, ...]
    edges: tuple[GeneratedEdge, ...]
    seed: int
    difficulty: Difficulty
    initial_crossing_count: int
    crossing_count: int
    target_crossing_count: int
    completed: bool = False


def _circle_points(count: int, *, center: float = 0.5, radius: float = 0.39) -> tuple[Point, ...]:
    return tuple(
        Point(
            center + radius * math.cos((2.0 * math.pi * index / count) - math.pi / 2.0),
            center + radius * math.sin((2.0 * math.pi * index / count) - math.pi / 2.0),
        )
        for index in range(count)
    )


def _cycle_edges(count: int) -> set[tuple[int, int]]:
    return {tuple(sorted((index, (index + 1) % count))) for index in range(count)}


def _random_planar_edges(
    count: int,
    target_positions: tuple[Point, ...],
    rng: random.Random,
    *,
    diagonal_target: int,
) -> tuple[Segment, ...]:
    """Build a seeded, connected planar graph in the convex target embedding.

    The cycle guarantees connectivity.  Randomly ordered diagonals are added
    only when they do not cross an already selected edge, yielding a
    triangulation-sized non-crossing family without privileging one hub node.
    """
    boundary = _cycle_edges(count)
    candidates = [
        tuple(sorted((first, second)))
        for first in range(count)
        for second in range(first + 1, count)
        if tuple(sorted((first, second))) not in boundary
    ]
    diagonal_target = max(0, min(count - 3, diagonal_target))
    for _attempt in range(32):
        rng.shuffle(candidates)
        selected = set(boundary)
        for a, b in candidates:
            candidate = Segment(a, b)
            if any(
                edge_pair_crosses(target_positions, candidate, Segment(left, right))
                for left, right in selected
            ):
                continue
            selected.add((a, b))
            if len(selected) - len(boundary) >= diagonal_target:
                return tuple(Segment(left, right) for left, right in sorted(selected))
    raise RuntimeError(f"could not generate a planar topology for {count} points")


def _scramble_order(
    count: int,
    rng: random.Random,
    stationary_node_ids: frozenset[int],
) -> list[int] | None:
    movable = [index for index in range(count) if index not in stationary_node_ids]
    if len(movable) < 2:
        return None
    for _ in range(100):
        shuffled = movable[:]
        rng.shuffle(shuffled)
        if all(shuffled[index] != movable[index] for index in range(len(movable))):
            order = list(range(count))
            for index, point_id in zip(movable, shuffled):
                order[index] = point_id
            return order
    return None


def generate_puzzle(
    difficulty: Difficulty | str,
    *,
    seed: int,
    point_count: int | None = None,
    minimum_crossings: int | None = None,
    diagonal_count: int | None = None,
    stationary_node_ids: Iterable[int] = (),
) -> GeneratedPuzzle:
    try:
        difficulty = difficulty if isinstance(difficulty, Difficulty) else Difficulty(difficulty)
    except ValueError as exc:
        raise ValueError(f"unsupported Untangle difficulty: {difficulty!r}") from exc
    rng = random.Random(int(seed))
    count = difficulty.point_count if point_count is None else int(point_count)
    if count < 4:
        raise ValueError("Untangle puzzles require at least four points")
    threshold = difficulty.minimum_crossings if minimum_crossings is None else int(minimum_crossings)
    if threshold < 0:
        raise ValueError("minimum_crossings must be non-negative")
    stationary = frozenset(int(node_id) for node_id in stationary_node_ids)
    if any(node_id < 0 or node_id >= count for node_id in stationary):
        raise ValueError("stationary node id is outside the puzzle")
    requested_diagonals = count - 3 if diagonal_count is None else int(diagonal_count)
    if requested_diagonals < 0 or requested_diagonals > count - 3:
        raise ValueError("diagonal_count must be between 0 and count - 3")
    target = _circle_points(count)
    target_positions = tuple(GeneratedPoint(index, point.x, point.y) for index, point in enumerate(target))

    for _topology_attempt in range(32):
        edges = _random_planar_edges(
            count,
            target,
            rng,
            diagonal_target=requested_diagonals,
        )
        target_crossings = crossing_pair_count(target, edges)
        if target_crossings != 0:
            raise RuntimeError("internal planar target construction is not planar")
        for _attempt in range(2000):
            order = _scramble_order(count, rng, stationary)
            if order is None:
                continue
            scrambled = tuple(target[order[index]] for index in range(count))
            initial_crossings = crossing_pair_count(scrambled, edges)
            if initial_crossings >= threshold:
                points = tuple(GeneratedPoint(index, point.x, point.y) for index, point in enumerate(scrambled))
                generated_edges = tuple(GeneratedEdge(edge.a, edge.b) for edge in edges)
                return GeneratedPuzzle(
                    points=points,
                    target_positions=target_positions,
                    edges=generated_edges,
                    seed=int(seed),
                    difficulty=difficulty,
                    initial_crossing_count=initial_crossings,
                    crossing_count=initial_crossings,
                    target_crossing_count=target_crossings,
                )
    raise RuntimeError(
        f"could not generate a non-trivial Untangle puzzle for {difficulty.value} seed {seed}"
    )
