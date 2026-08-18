"""Qt-free mutable model for the Untangle candidate."""

from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Iterable

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
class UntangleHint:
    level: int
    node_id: int | None
    edge_indices: frozenset[int] = frozenset()
    direction: str | None = None


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
    fixed_node_ids: frozenset[int] = frozenset()


class UntangleModel:
    """Model authority for graph, positions, history, and completion."""

    MIN_POINT_SEPARATION = 0.035

    def __init__(
        self,
        *,
        difficulty: Difficulty | str = Difficulty.EASY,
        seed: int | None = None,
        point_count: int | None = None,
        minimum_crossings: int | None = None,
        diagonal_count: int | None = None,
        fixed_node_ids: Iterable[int] = (),
    ) -> None:
        self.difficulty = difficulty if isinstance(difficulty, Difficulty) else Difficulty(difficulty)
        self._point_count = point_count
        self._minimum_crossings = minimum_crossings
        self._diagonal_count = diagonal_count
        self._fixed_node_ids = frozenset(int(node_id) for node_id in fixed_node_ids)
        self._seed_rng = random.Random(seed)
        self.seed = int(seed if seed is not None else self._seed_rng.randrange(2**31))
        self._history: list[tuple[UntanglePoint, ...]] = []
        self._drag_start: tuple[UntanglePoint, ...] | None = None
        self._hint: UntangleHint | None = None
        self._generated: GeneratedPuzzle = self._generate()
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
            fixed_node_ids=self._fixed_node_ids,
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

    @property
    def fixed_node_ids(self) -> frozenset[int]:
        return self._fixed_node_ids

    @property
    def hint_level(self) -> int:
        return self._hint.level if self._hint is not None else 0

    @property
    def hint_node_id(self) -> int | None:
        return self._hint.node_id if self._hint is not None else None

    @property
    def hint_edge_indices(self) -> frozenset[int]:
        return self._hint.edge_indices if self._hint is not None else frozenset()

    @property
    def hint_direction(self) -> str | None:
        return self._hint.direction if self._hint is not None else None

    def begin_drag(self, point_id: int) -> bool:
        if self._completed or not self._valid_point_id(point_id) or point_id in self._fixed_node_ids:
            return False
        self._drag_start = self._points
        return True

    def drag_point(self, point_id: int, x: float, y: float) -> bool:
        if self._completed or not self._valid_point_id(point_id):
            return False
        position = UntanglePoint(point_id, self._clamp(x), self._clamp(y))
        if not self._position_is_legal(position):
            return False
        self._points = tuple(position if point.id == point_id else point for point in self._points)
        self._recompute()
        self._hint = None
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
        target = UntanglePoint(point_id, self._clamp(x), self._clamp(y))
        changed = False
        if self._position_is_legal(target):
            self._points = tuple(target if point.id == point_id else point for point in self._points)
            self._recompute()
            changed = True
        else:
            # The programmatic helper is also used to apply a known target
            # permutation in tests. If that target is occupied, exchange the
            # two nodes rather than ever creating an overlapping position.
            occupant = next(
                (
                    point
                    for point in self._points
                    if point.id != point_id
                    and point.x == target.x
                    and point.y == target.y
                ),
                None,
            )
            if occupant is not None:
                origin = self._points[point_id]
                self._points = tuple(
                    target
                    if point.id == point_id
                    else UntanglePoint(point.id, origin.x, origin.y)
                    if point.id == occupant.id
                    else point
                    for point in self._points
                )
                self._recompute()
                changed = True
        if changed:
            self._hint = None
        self.end_drag()
        return changed

    def undo(self) -> bool:
        if not self._history:
            return False
        self._points = self._history.pop()
        self._drag_start = None
        self._hint = None
        self._recompute()
        return True

    def reset(self) -> bool:
        self._points = tuple(self._generated.points[index] for index in range(len(self._generated.points)))
        self._points = tuple(UntanglePoint(point.id, point.x, point.y) for point in self._points)
        self._history.clear()
        self._drag_start = None
        self._hint = None
        self._recompute()
        return True

    def new_puzzle(
        self,
        *,
        seed: int | None = None,
        difficulty: Difficulty | str | None = None,
        fixed_node_ids: Iterable[int] | None = None,
    ) -> bool:
        if difficulty is not None:
            self.difficulty = difficulty if isinstance(difficulty, Difficulty) else Difficulty(difficulty)
        if fixed_node_ids is not None:
            self._fixed_node_ids = frozenset(int(node_id) for node_id in fixed_node_ids)
        old_seed = self.seed
        self.seed = int(seed if seed is not None else self._seed_rng.randrange(2**31))
        if seed is None and self.seed == old_seed:
            self.seed = old_seed + 1
        self._generated = self._generate()
        self._points = tuple(UntanglePoint(point.id, point.x, point.y) for point in self._generated.points)
        self._target_positions = tuple(UntanglePoint(point.id, point.x, point.y) for point in self._generated.target_positions)
        self._edges = tuple(UntangleEdge(edge.a, edge.b) for edge in self._generated.edges)
        self._history.clear()
        self._drag_start = None
        self._hint = None
        self._recompute()
        return True

    def request_hint(self) -> "UntangleHint":
        if self._completed:
            return UntangleHint(level=0, node_id=None, direction="已解开")
        if self._hint is None:
            self._hint = self._build_hint(1)
        elif self._hint.level < 3:
            self._hint = self._build_hint(self._hint.level + 1)
        return self._hint

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

    def _generate(self) -> GeneratedPuzzle:
        return generate_puzzle(
            self.difficulty,
            seed=self.seed,
            point_count=self._point_count,
            minimum_crossings=self._minimum_crossings,
            diagonal_count=self._diagonal_count,
            stationary_node_ids=self._fixed_node_ids,
        )

    def _build_hint(self, level: int) -> "UntangleHint":
        crossing_edge_indices = self._crossing_edges
        incident_counts = {point.id: 0 for point in self._points}
        for edge_index in crossing_edge_indices:
            edge = self._edges[edge_index]
            incident_counts[edge.a] += 1
            incident_counts[edge.b] += 1
        movable = [point_id for point_id in incident_counts if point_id not in self._fixed_node_ids]
        candidates = movable or list(incident_counts)
        node_id = max(candidates, key=lambda point_id: (incident_counts[point_id], -point_id))
        node_edges = frozenset(
            edge_index
            for edge_index in crossing_edge_indices
            if node_id in (self._edges[edge_index].a, self._edges[edge_index].b)
        )
        direction = None
        if level >= 3:
            current = self._points[node_id]
            target = self._target_positions[node_id]
            horizontal = "右" if target.x - current.x > 0.02 else "左" if current.x - target.x > 0.02 else ""
            vertical = "下" if target.y - current.y > 0.02 else "上" if current.y - target.y > 0.02 else ""
            direction = f"向{vertical}{horizontal}方调整" if vertical or horizontal else "在附近微调"
        return UntangleHint(level=level, node_id=node_id, edge_indices=node_edges, direction=direction)

    def _valid_point_id(self, point_id: int) -> bool:
        return isinstance(point_id, int) and 0 <= point_id < len(self._points)

    def _position_is_legal(self, position: UntanglePoint) -> bool:
        minimum_squared = self.MIN_POINT_SEPARATION**2
        return all(
            (position.x - other.x) ** 2 + (position.y - other.y) ** 2 >= minimum_squared
            for other in self._points
            if other.id != position.id
        )

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.04, min(0.96, float(value)))
