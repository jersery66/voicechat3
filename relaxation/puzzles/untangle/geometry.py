"""Pure segment geometry for the Untangle puzzle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


EPSILON = 1e-9


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Segment:
    a: int
    b: int


def _orientation(a: Point, b: Point, c: Point) -> float:
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def _on_segment(a: Point, b: Point, c: Point) -> bool:
    return (
        min(a.x, b.x) - EPSILON <= c.x <= max(a.x, b.x) + EPSILON
        and min(a.y, b.y) - EPSILON <= c.y <= max(a.y, b.y) + EPSILON
    )


def segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    """Return true for proper, endpoint, or collinear-overlap intersection."""
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)

    if ((o1 > EPSILON and o2 < -EPSILON) or (o1 < -EPSILON and o2 > EPSILON)) and (
        (o3 > EPSILON and o4 < -EPSILON) or (o3 < -EPSILON and o4 > EPSILON)
    ):
        return True
    if abs(o1) <= EPSILON and _on_segment(a, b, c):
        return True
    if abs(o2) <= EPSILON and _on_segment(a, b, d):
        return True
    if abs(o3) <= EPSILON and _on_segment(c, d, a):
        return True
    if abs(o4) <= EPSILON and _on_segment(c, d, b):
        return True
    return False


def edge_pair_crosses(points: Sequence[Point], first: Segment, second: Segment) -> bool:
    """Apply Untangle's shared-node rule before geometric intersection."""
    if {first.a, first.b} & {second.a, second.b}:
        return False
    return segments_intersect(
        points[first.a], points[first.b], points[second.a], points[second.b]
    )


def crossing_pairs(points: Sequence[Point], edges: Sequence[Segment]) -> tuple[tuple[int, int], ...]:
    pairs: list[tuple[int, int]] = []
    for first_index, first in enumerate(edges):
        for second_index in range(first_index + 1, len(edges)):
            if edge_pair_crosses(points, first, edges[second_index]):
                pairs.append((first_index, second_index))
    return tuple(pairs)


def crossing_edge_indices(points: Sequence[Point], edges: Sequence[Segment]) -> frozenset[int]:
    return frozenset(index for pair in crossing_pairs(points, edges) for index in pair)


def crossing_pair_count(points: Sequence[Point], edges: Sequence[Segment]) -> int:
    return len(crossing_pairs(points, edges))
