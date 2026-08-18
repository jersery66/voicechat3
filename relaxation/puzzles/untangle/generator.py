"""Seeded planar graph generation for the Untangle candidate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import random

from .geometry import Point, Segment, crossing_pair_count


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


def _planar_cycle_fan(count: int) -> tuple[Segment, ...]:
    # A cycle plus a fan from node 0 is planar in the convex target embedding;
    # it gives every node a meaningful degree and enough edges for crossings.
    edges = {tuple(sorted((index, (index + 1) % count))) for index in range(count)}
    edges.update(tuple(sorted((0, index))) for index in range(2, count - 1))
    return tuple(Segment(a, b) for a, b in sorted(edges))


def generate_puzzle(difficulty: Difficulty | str, *, seed: int) -> GeneratedPuzzle:
    try:
        difficulty = difficulty if isinstance(difficulty, Difficulty) else Difficulty(difficulty)
    except ValueError as exc:
        raise ValueError(f"unsupported Untangle difficulty: {difficulty!r}") from exc
    rng = random.Random(int(seed))
    count = difficulty.point_count
    target = _circle_points(count)
    edges = _planar_cycle_fan(count)
    target_crossings = crossing_pair_count(target, edges)
    if target_crossings != 0:
        raise RuntimeError("internal planar target construction is not planar")

    for _attempt in range(2000):
        order = list(range(count))
        rng.shuffle(order)
        scrambled = tuple(target[order[index]] for index in range(count))
        initial_crossings = crossing_pair_count(scrambled, edges)
        if initial_crossings >= difficulty.minimum_crossings:
            points = tuple(GeneratedPoint(index, point.x, point.y) for index, point in enumerate(scrambled))
            target_positions = tuple(GeneratedPoint(index, point.x, point.y) for index, point in enumerate(target))
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
