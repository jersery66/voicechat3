"""Pure geometry contracts for V2-A Untangle."""

from __future__ import annotations

from relaxation.puzzles.untangle.geometry import (
    Point,
    Segment,
    crossing_edge_indices,
    crossing_pair_count,
    segments_intersect,
)


def test_crossing_x_is_detected():
    assert segments_intersect(
        Point(0.0, 0.0), Point(1.0, 1.0), Point(0.0, 1.0), Point(1.0, 0.0)
    ) is True


def test_parallel_segments_do_not_cross():
    assert segments_intersect(
        Point(0.0, 0.0), Point(1.0, 0.0), Point(0.0, 1.0), Point(1.0, 1.0)
    ) is False


def test_collinear_overlap_and_endpoint_touch_are_intersections():
    assert segments_intersect(
        Point(0.0, 0.0), Point(1.0, 0.0), Point(0.5, 0.0), Point(1.5, 0.0)
    ) is True
    assert segments_intersect(
        Point(0.0, 0.0), Point(1.0, 0.0), Point(1.0, 0.0), Point(1.0, 1.0)
    ) is True


def test_shared_graph_endpoint_is_not_a_crossing():
    points = (Point(0.0, 0.0), Point(1.0, 0.0), Point(0.0, 1.0))
    edges = (Segment(0, 1), Segment(0, 2))
    assert crossing_edge_indices(points, edges) == frozenset()
    assert crossing_pair_count(points, edges) == 0


def test_moving_a_point_changes_crossing_feedback():
    points = (
        Point(0.0, 0.0), Point(1.0, 1.0), Point(0.0, 1.0), Point(1.0, 0.0)
    )
    edges = (Segment(0, 1), Segment(2, 3))
    assert crossing_pair_count(points, edges) == 1
    moved = (points[0], Point(0.2, 0.0), points[2], points[3])
    assert crossing_pair_count(moved, edges) == 0
