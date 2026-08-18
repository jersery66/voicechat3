"""Qt-free mutable model for the Untangle candidate."""

from __future__ import annotations

from dataclasses import dataclass
import random

from .generator import Difficulty, GeneratedPuzzle, generate_puzzle
from .geometry import Point, Segment, crossing_edge_indices, crossing_pair_count


@dataclass(frozen=True)
class UntanglePoint:
    id: int
    x: float
    y: float


@dataclass(frozen=True)
class UntangleEdge:
    a: int
    b: int


@dataclass(frozen=True)
class UntangleState:
    points: tuple[UntanglePoint, ...]
    target_positions: tuple[UntanglePoint, ...]
    edges: tuple[UntangleEdge, ...]
    crossing_edges: frozenset[int]
    crossing_count: int
    initial_crossing_count: int
    completed: bool
    seed: int
    difficulty: Difficulty


class UntangleModel:
    """Model authority for graph, positions, history, and completion."""

    MIN_POINT_SEPARATION = 0.035

    def __init__(self, *, difficulty: Difficulty | str = Difficulty.EASY, seed: int | None = None) -> None:
        self.difficulty = difficulty if isinstance(difficulty, Difficulty) else Difficulty(difficulty)
        self._seed_rng = random.Random(seed)
        self.seed = int(seed if seed is not None else self._seed_rng.randrange(2**31))
        self._history: list[tuple[UntanglePoint, ...]] = []
        self._drag_start: tuple[UntanglePoint, ...] | None = None
        self._generated: GeneratedPuzzle = generate_puzzle(self.difficulty, seed=self.seed)
        self._points = tuple(UntanglePoint(point.id, point.x, point.y) for point in self._generated.points)
        self._target_positions = tuple(UntanglePoint(point.id, point.x, point.y) for point in self._generated.target_positions)
        self._edges = tuple(UntangleEdge(edge.a, edge.b) for edge in self._generated.edges)
        self._recompute()

    @property
    def state(self) -> UntangleState:
        return UntangleState(
            points=self._points,
            target_positions=self._target_positions,
            edges=self._edges,
            crossing_edges=self._crossing_edges,
            crossing_count=self._crossing_count,
            initial_crossing_count=self._generated.initial_crossing_count,
            completed=self._completed,
            seed=self.seed,
            difficulty=self.difficulty,
        )

    @property
    def points(self) -> tuple[UntanglePoint, ...]:
        return self._points

    @property
    def edges(self) -> tuple[UntangleEdge, ...]:
        return self._edges

    @property
    def target_positions(self) -> tuple[UntanglePoint, ...]:
        return self._target_positions

    @property
    def crossing_edges(self) -> frozenset[int]:
        return self._crossing_edges

    @property
    def crossing_count(self) -> int:
        return self._crossing_count

    @property
    def completed(self) -> bool:
        return self._completed

    @property
    def can_undo(self) -> bool:
        return bool(self._history)

    def begin_drag(self, point_id: int) -> bool:
        if self._completed or not self._valid_point_id(point_id):
            return False
        self._drag_start = self._points
        return True

    def drag_point(self, point_id: int, x: float, y: float) -> bool:
        if self._completed or not self._valid_point_id(point_id):
            return False
        position = UntanglePoint(point_id, self._clamp(x), self._clamp(y))
        self._points = tuple(position if point.id == point_id else point for point in self._points)
        self._recompute()
        return True

    def end_drag(self) -> bool:
        if self._drag_start is None:
            return False
        changed = self._drag_start != self._points
        if changed:
            self._history.append(self._drag_start)
        self._drag_start = None
        return changed

    def move_point(self, point_id: int, x: float, y: float) -> bool:
        if not self.begin_drag(point_id):
            return False
        changed = self.drag_point(point_id, x, y)
        self.end_drag()
        return changed

    def undo(self) -> bool:
        if not self._history:
            return False
        self._points = self._history.pop()
        self._drag_start = None
        self._recompute()
        return True

    def reset(self) -> bool:
        self._points = tuple(self._generated.points[index] for index in range(len(self._generated.points)))
        self._points = tuple(UntanglePoint(point.id, point.x, point.y) for point in self._points)
        self._history.clear()
        self._drag_start = None
        self._recompute()
        return True

    def new_puzzle(self, *, seed: int | None = None, difficulty: Difficulty | str | None = None) -> bool:
        if difficulty is not None:
            self.difficulty = difficulty if isinstance(difficulty, Difficulty) else Difficulty(difficulty)
        old_seed = self.seed
        self.seed = int(seed if seed is not None else self._seed_rng.randrange(2**31))
        if seed is None and self.seed == old_seed:
            self.seed = old_seed + 1
        self._generated = generate_puzzle(self.difficulty, seed=self.seed)
        self._points = tuple(UntanglePoint(point.id, point.x, point.y) for point in self._generated.points)
        self._target_positions = tuple(UntanglePoint(point.id, point.x, point.y) for point in self._generated.target_positions)
        self._edges = tuple(UntangleEdge(edge.a, edge.b) for edge in self._generated.edges)
        self._history.clear()
        self._drag_start = None
        self._recompute()
        return True

    def hit_test(self, x: float, y: float, radius: float = 0.055) -> int | None:
        candidates = [
            (point.id, (point.x - x) ** 2 + (point.y - y) ** 2)
            for point in self._points
            if (point.x - x) ** 2 + (point.y - y) ** 2 <= radius**2
        ]
        return min(candidates, key=lambda item: item[1])[0] if candidates else None

    def _recompute(self) -> None:
        points = tuple(Point(point.x, point.y) for point in self._points)
        edges = tuple(Segment(edge.a, edge.b) for edge in self._edges)
        self._crossing_edges = crossing_edge_indices(points, edges)
        self._crossing_count = crossing_pair_count(points, edges)
        self._completed = self._crossing_count == 0

    def _valid_point_id(self, point_id: int) -> bool:
        return isinstance(point_id, int) and 0 <= point_id < len(self._points)

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.04, min(0.96, float(value)))
