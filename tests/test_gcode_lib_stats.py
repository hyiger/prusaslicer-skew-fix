"""Tests for gcode_lib statistics: compute_bounds, compute_stats."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gcode_lib as gl


# ---------------------------------------------------------------------------
# Bounds dataclass
# ---------------------------------------------------------------------------

def test_bounds_starts_invalid():
    b = gl.Bounds()
    assert not b.valid


def test_bounds_expand_makes_valid():
    b = gl.Bounds()
    b.expand(5.0, 10.0)
    assert b.valid


def test_bounds_expand_updates_min_max():
    b = gl.Bounds()
    b.expand(5.0, 10.0)
    b.expand(2.0, 20.0)
    b.expand(8.0, 1.0)
    assert b.x_min == pytest.approx(2.0)
    assert b.x_max == pytest.approx(8.0)
    assert b.y_min == pytest.approx(1.0)
    assert b.y_max == pytest.approx(20.0)


def test_bounds_width_height():
    b = gl.Bounds()
    b.expand(0.0, 0.0)
    b.expand(100.0, 50.0)
    assert b.width == pytest.approx(100.0)
    assert b.height == pytest.approx(50.0)


def test_bounds_center():
    b = gl.Bounds()
    b.expand(0.0, 0.0)
    b.expand(100.0, 60.0)
    assert b.center_x == pytest.approx(50.0)
    assert b.center_y == pytest.approx(30.0)


def test_bounds_expand_z():
    b = gl.Bounds()
    b.expand_z(0.2)
    b.expand_z(0.4)
    b.expand_z(0.1)
    assert b.z_min == pytest.approx(0.1)
    assert b.z_max == pytest.approx(0.4)


def test_bounds_width_height_invalid():
    b = gl.Bounds()
    assert b.width == pytest.approx(0.0)
    assert b.height == pytest.approx(0.0)
    assert b.center_x == pytest.approx(0.0)
    assert b.center_y == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# compute_bounds
# ---------------------------------------------------------------------------

def test_compute_bounds_basic_moves():
    lines  = gl.parse_lines("G90\nG1 X0 Y0\nG1 X100 Y50\nG1 X-10 Y200")
    bounds = gl.compute_bounds(lines)
    assert bounds.valid
    assert bounds.x_min == pytest.approx(-10.0)
    assert bounds.x_max == pytest.approx(100.0)
    assert bounds.y_min == pytest.approx(0.0)
    assert bounds.y_max == pytest.approx(200.0)


def test_compute_bounds_z_tracked():
    lines  = gl.parse_lines("G90\nG1 Z0.2\nG1 Z0.4\nG1 Z0.1")
    bounds = gl.compute_bounds(lines)
    assert bounds.z_min == pytest.approx(0.1)
    assert bounds.z_max == pytest.approx(0.4)


def test_compute_bounds_empty_no_moves():
    bounds = gl.compute_bounds([])
    assert not bounds.valid


def test_compute_bounds_extruding_only_excludes_travels():
    lines  = gl.parse_lines("G90\nM82\nG1 X5 Y5\nG1 X10 Y10 E1.0\nG1 X200 Y200")
    bounds = gl.compute_bounds(lines, extruding_only=True)
    # Only the extruding move (X10, Y10) should be included
    assert bounds.x_max == pytest.approx(10.0)
    assert bounds.y_max == pytest.approx(10.0)
    assert bounds.x_min == pytest.approx(10.0)


def test_compute_bounds_includes_arc_points():
    # Quarter-circle arc from (10,0) to (0,10) centred at (0,0)
    lines  = gl.parse_lines("G90\nG1 X10 Y0\nG3 X0 Y10 I-10 J0")
    bounds = gl.compute_bounds(lines, include_arcs=True)
    # Arc passes through points with x and y up to 10
    assert bounds.x_max == pytest.approx(10.0, abs=0.5)
    assert bounds.y_max == pytest.approx(10.0, abs=0.5)


def test_compute_bounds_excludes_arcs_when_disabled():
    lines  = gl.parse_lines("G90\nG1 X10 Y0\nG3 X0 Y10 I-10 J0")
    bounds = gl.compute_bounds(lines, include_arcs=False)
    # Only the initial G1 X10 Y0 should be counted
    assert bounds.x_max == pytest.approx(10.0)
    assert bounds.y_max == pytest.approx(0.0)


def test_compute_bounds_relative_xy():
    lines  = gl.parse_lines("G90\nG1 X0 Y0\nG91\nG1 X10 Y5\nG1 X5 Y5")
    bounds = gl.compute_bounds(lines)
    assert bounds.x_max == pytest.approx(15.0)
    assert bounds.y_max == pytest.approx(10.0)


def test_compute_bounds_comment_only_lines_ignored():
    lines  = gl.parse_lines("G90\n; just a comment\nG1 X10 Y20")
    bounds = gl.compute_bounds(lines)
    assert bounds.valid
    assert bounds.x_max == pytest.approx(10.0)


def test_compute_bounds_no_xy_move_no_bounds():
    lines = gl.parse_lines("G90\nG1 Z0.2\nM82")
    bounds = gl.compute_bounds(lines)
    assert not bounds.valid


# ---------------------------------------------------------------------------
# compute_stats
# ---------------------------------------------------------------------------

def test_compute_stats_total_lines():
    lines = gl.parse_lines("G90\nM82\nG1 X10 Y20 E1.0\n; comment")
    stats = gl.compute_stats(lines)
    assert stats.total_lines == 4


def test_compute_stats_blank_lines():
    lines = gl.parse_lines("G90\n\n\nG1 X10")
    stats = gl.compute_stats(lines)
    assert stats.blank_lines == 2


def test_compute_stats_comment_only_lines():
    lines = gl.parse_lines("; a\n; b\nG90")
    stats = gl.compute_stats(lines)
    assert stats.comment_only_lines == 2


def test_compute_stats_move_count():
    lines = gl.parse_lines("G90\nG1 X5\nG0 X10\nG1 X15 E1.0")
    stats = gl.compute_stats(lines)
    assert stats.move_count == 3


def test_compute_stats_arc_count():
    lines = gl.parse_lines("G90\nG1 X10 Y0\nG2 X0 Y10 I-10 J0\nG3 X10 Y0 I10 J0")
    stats = gl.compute_stats(lines)
    assert stats.arc_count == 2


def test_compute_stats_extrude_count():
    lines = gl.parse_lines("G90\nM82\nG1 X5 E0\nG1 X10 E1.0\nG1 X15 E2.0")
    stats = gl.compute_stats(lines)
    assert stats.extrude_count == 2  # E0->1.0 and E1.0->2.0


def test_compute_stats_travel_count():
    lines = gl.parse_lines("G90\nM82\nG1 X5\nG1 X10 E1.0\nG1 X15")
    stats = gl.compute_stats(lines)
    assert stats.travel_count == 2  # G1 X5 and G1 X15


def test_compute_stats_retract_count():
    lines = gl.parse_lines("G90\nM82\nG1 X5 E1.0\nG1 X5 E0.5")  # E decreasing
    stats = gl.compute_stats(lines)
    assert stats.retract_count == 1


def test_compute_stats_total_extrusion():
    lines = gl.parse_lines("G90\nM82\nG1 X5 E0\nG1 X10 E1.5\nG1 X15 E3.0")
    stats = gl.compute_stats(lines)
    assert stats.total_extrusion == pytest.approx(3.0)


def test_compute_stats_total_extrusion_rel():
    lines = gl.parse_lines("G90\nM83\nG1 X5 E0.5\nG1 X10 E0.3\nG1 X15 E0.2")
    stats = gl.compute_stats(lines)
    assert stats.total_extrusion == pytest.approx(1.0)


def test_compute_stats_z_heights():
    lines = gl.parse_lines("G90\nG1 Z0.2\nG1 X5\nG1 Z0.4\nG1 X10\nG1 Z0.4")
    stats = gl.compute_stats(lines)
    assert stats.z_heights == pytest.approx([0.2, 0.4])


def test_compute_stats_layer_count():
    lines = gl.parse_lines("G90\nG1 Z0.2\nG1 Z0.4\nG1 Z0.6")
    stats = gl.compute_stats(lines)
    assert stats.layer_count == 3


def test_compute_stats_feedrates():
    lines = gl.parse_lines("G90\nG1 X5 F3000\nG1 X10 F1500\nG1 X15 F3000")
    stats = gl.compute_stats(lines)
    # Unique feedrates in order of first occurrence
    assert 3000.0 in stats.feedrates
    assert 1500.0 in stats.feedrates
    assert stats.feedrates[0] == pytest.approx(3000.0)
    assert stats.feedrates[1] == pytest.approx(1500.0)
    assert len(stats.feedrates) == 2  # 3000 seen twice but only counted once


def test_compute_stats_bounds_populated():
    lines = gl.parse_lines("G90\nG1 X10 Y20\nG1 X5 Y30")
    stats = gl.compute_stats(lines)
    assert stats.bounds.valid
    assert stats.bounds.x_min == pytest.approx(5.0)
    assert stats.bounds.x_max == pytest.approx(10.0)
    assert stats.bounds.y_max == pytest.approx(30.0)


def test_compute_stats_empty():
    stats = gl.compute_stats([])
    assert stats.total_lines == 0
    assert not stats.bounds.valid


def test_compute_stats_arcs_contribute_to_bounds():
    # A quarter-circle from (10,0) to (0,10) should contribute points in between
    lines = gl.parse_lines("G90\nG1 X10 Y0\nG3 X0 Y10 I-10 J0")
    stats = gl.compute_stats(lines)
    assert stats.arc_count == 1
    assert stats.bounds.x_max == pytest.approx(10.0, abs=0.5)
    assert stats.bounds.y_max == pytest.approx(10.0, abs=0.5)


# ---------------------------------------------------------------------------
# GCodeStats.layer_count convenience property
# ---------------------------------------------------------------------------

def test_stats_layer_count_zero_when_no_z():
    lines = gl.parse_lines("G90\nG1 X10 Y20 E1.0")
    stats = gl.compute_stats(lines)
    assert stats.layer_count == 0
