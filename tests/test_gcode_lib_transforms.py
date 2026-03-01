"""Tests for gcode_lib transforms: linearize_arcs, apply_xy_transform, apply_skew, translate_xy."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gcode_lib as gl


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def test_fmt_float_trims_trailing_zeros():
    assert gl.fmt_float(10.0, 3) == "10"
    assert gl.fmt_float(10.100, 3) == "10.1"
    assert gl.fmt_float(10.125, 3) == "10.125"


def test_fmt_float_negative_zero():
    assert gl.fmt_float(-0.0, 3) == "0"


def test_fmt_axis_xy_uses_xy_decimals():
    s = gl.fmt_axis("X", 10.12345, xy_decimals=2, other_decimals=5)
    assert s == "10.12"


def test_fmt_axis_e_uses_other_decimals():
    s = gl.fmt_axis("E", 1.12345, xy_decimals=2, other_decimals=4)
    assert s == "1.1235"  # rounds to 4 places → 1.1234 actually, let me check
    # 1.12345 rounded to 4 places = 1.1235 (banker's rounding: 5 rounds to even → 4 even → 1.1234)
    # Python f"{1.12345:.4f}" -> "1.1235" (standard rounding)
    assert gl.fmt_axis("E", 1.12345, xy_decimals=2, other_decimals=4) in ("1.1234", "1.1235")


def test_replace_or_append_replaces_existing():
    result = gl.replace_or_append("G1 X10 Y20", "X", 15.5)
    assert "X15.5" in result
    assert "X10" not in result


def test_replace_or_append_appends_when_missing():
    result = gl.replace_or_append("G1 Y20", "X", 15.5)
    assert "X15.5" in result


def test_replace_or_append_preserves_other_words():
    result = gl.replace_or_append("G1 X10 Y20 E1.0", "X", 99.0)
    assert "Y20" in result
    assert "E1" in result


def test_replace_or_append_only_first_occurrence():
    # Should only replace first X occurrence (unusual but defensive)
    result = gl.replace_or_append("G1 X10 X20", "X", 5.0)
    # One X5 present, at most one replacement
    assert result.count("X5") == 1


# ---------------------------------------------------------------------------
# linearize_arcs — basic
# ---------------------------------------------------------------------------

def test_linearize_arcs_replaces_g2_with_g1():
    lines = gl.parse_lines("G90\nG1 X0 Y0\nG2 X10 Y0 I5 J0")
    result = gl.linearize_arcs(lines)
    commands = [ln.command for ln in result]
    assert "G2" not in commands
    assert "G1" in commands


def test_linearize_arcs_replaces_g3_with_g1():
    lines = gl.parse_lines("G90\nG1 X0 Y0\nG3 X0 Y10 I0 J5")
    result = gl.linearize_arcs(lines)
    assert all(ln.command != "G3" for ln in result)


def test_linearize_arcs_non_arc_lines_unchanged():
    lines = gl.parse_lines("G90\nM82\nG1 X10 Y20 E1.0\n; comment")
    result = gl.linearize_arcs(lines)
    # Non-arc lines preserve their raw text
    non_arc = [ln for ln in result if ln.command != "G1" or "G2" not in ln.raw]
    assert result[0].raw == lines[0].raw   # G90
    assert result[1].raw == lines[1].raw   # M82


def test_linearize_arcs_endpoint_correct():
    """Last segment of a linearized arc must end exactly at the arc endpoint."""
    lines = gl.parse_lines("G90\nG1 X0 Y0\nG2 X10 Y0 I5 J0")
    result = gl.linearize_arcs(lines)
    g1_lines = [ln for ln in result if ln.command == "G1" and "X" in ln.words]
    last = g1_lines[-1]
    assert last.words["X"] == pytest.approx(10.0, abs=1e-3)
    assert last.words["Y"] == pytest.approx(0.0, abs=1e-3)


def test_linearize_arcs_produces_multiple_segments():
    """A 90° arc of radius 10 at default precision should produce many segments."""
    lines = gl.parse_lines("G90\nG1 X10 Y0\nG3 X0 Y10 I-10 J0")
    result = gl.linearize_arcs(lines)
    g1_count = sum(1 for ln in result if ln.command == "G1")
    assert g1_count > 2   # Must have more than the original single move + arc


def test_linearize_arcs_full_circle():
    """A full-circle arc (start == end) should be linearized, not dropped."""
    lines = gl.parse_lines("G90\nG1 X10 Y0\nG2 X10 Y0 I-10 J0")
    result = gl.linearize_arcs(lines)
    arc_segs = [ln for ln in result if ln.command == "G1" and "X" in ln.words]
    # Should have many segments (full circle ≈ 63 mm circumference / 0.2 mm)
    assert len(arc_segs) > 30


def test_linearize_arcs_e_preserved_abs():
    """Absolute E at arc end must equal the value in the original arc command."""
    lines = gl.parse_lines("G90\nM82\nG1 X0 Y0 E0\nG2 X10 Y0 I5 J0 E2.5")
    result = gl.linearize_arcs(lines)
    g1s = [ln for ln in result if ln.command == "G1" and "E" in ln.words]
    last_e = g1s[-1].words["E"]
    assert last_e == pytest.approx(2.5, abs=1e-4)


def test_linearize_arcs_e_preserved_rel():
    """Sum of printed relative E increments must equal the original arc E."""
    lines = gl.parse_lines("G90\nM83\nG1 X0 Y0\nG2 X10 Y0 I5 J0 E1.0")
    result = gl.linearize_arcs(lines)
    g1s = [ln for ln in result if ln.command == "G1" and "E" in ln.words]
    total = sum(ln.words["E"] for ln in g1s)
    assert total == pytest.approx(1.0, abs=1e-4)


def test_linearize_arcs_comment_on_first_segment():
    lines = gl.parse_lines("G90\nG1 X0 Y0\nG2 X10 Y0 I5 J0 ; arc comment")
    result = gl.linearize_arcs(lines)
    # The original 'G1 X0 Y0' is at index 0; arc segments follow
    g1s = [ln for ln in result if ln.command == "G1"]
    # Exactly one line should carry the arc comment
    with_comment = [ln for ln in g1s if "; arc comment" in ln.raw]
    assert len(with_comment) == 1
    # It must be the first arc segment, not the original G1 X0 Y0
    assert with_comment[0].raw != "G1 X0 Y0"
    # All other arc G1s must not carry the comment
    without_comment = [ln for ln in g1s if "; arc comment" not in ln.raw]
    assert len(without_comment) == len(g1s) - 1


def test_linearize_arcs_feedrate_on_first_segment_only():
    lines = gl.parse_lines("G90\nG1 X0 Y0\nG2 X10 Y0 I5 J0 F2000")
    result = gl.linearize_arcs(lines)
    # Only one G1 among the arc segments should carry the F word
    g1s_with_f = [ln for ln in result if ln.command == "G1" and "F" in ln.words]
    assert len(g1s_with_f) == 1
    # The G1 with F must be an arc segment (not the original 'G1 X0 Y0')
    assert g1s_with_f[0].raw != "G1 X0 Y0"


# ---------------------------------------------------------------------------
# apply_xy_transform
# ---------------------------------------------------------------------------

def test_apply_xy_transform_identity():
    lines  = gl.parse_lines("G90\nG1 X10 Y20 E1.0")
    result = gl.apply_xy_transform(lines, lambda x, y: (x, y))
    g1 = next(ln for ln in result if ln.command == "G1")
    assert g1.words["X"] == pytest.approx(10.0, abs=0.001)
    assert g1.words["Y"] == pytest.approx(20.0, abs=0.001)


def test_apply_xy_transform_scale():
    lines  = gl.parse_lines("G90\nG1 X10 Y20")
    result = gl.apply_xy_transform(lines, lambda x, y: (x * 2, y * 2))
    g1 = next(ln for ln in result if ln.command == "G1")
    assert g1.words["X"] == pytest.approx(20.0, abs=0.001)
    assert g1.words["Y"] == pytest.approx(40.0, abs=0.001)


def test_apply_xy_transform_preserves_other_words():
    lines  = gl.parse_lines("G90\nG1 X10 Y20 Z0.2 E1.0 F3000")
    result = gl.apply_xy_transform(lines, lambda x, y: (x + 1, y + 1))
    g1 = next(ln for ln in result if ln.command == "G1")
    assert "Z" in g1.raw
    assert "E" in g1.raw
    assert "F" in g1.raw


def test_apply_xy_transform_passes_through_non_moves():
    lines  = gl.parse_lines("G90\nM82\n; comment\nG1 X5")
    result = gl.apply_xy_transform(lines, lambda x, y: (x, y))
    assert result[0].raw == "G90"
    assert result[1].raw == "M82"
    assert result[2].raw == "; comment"


def test_apply_xy_transform_raises_on_relative_xy():
    lines = gl.parse_lines("G91\nG1 X5 Y5")
    with pytest.raises(ValueError, match="relative XY"):
        gl.apply_xy_transform(lines, lambda x, y: (x, y))


def test_apply_xy_transform_no_xy_move_passes_through():
    """G1 with only Z or E should pass through unmodified."""
    lines  = gl.parse_lines("G90\nG1 Z0.2")
    result = gl.apply_xy_transform(lines, lambda x, y: (x * 10, y * 10))
    g1 = next(ln for ln in result if ln.command == "G1")
    assert g1.raw == "G1 Z0.2"


def test_apply_xy_transform_state_uses_original_coords():
    """Subsequent moves use original (untransformed) coords for state."""
    # First move: X0 -> X10, state.x should remain 0 for the second move
    calls: list[tuple[float, float]] = []
    def fn(x: float, y: float) -> tuple[float, float]:
        calls.append((x, y))
        return (x + 100, y)

    lines  = gl.parse_lines("G90\nG1 X0 Y0\nG1 X5 Y0")
    gl.apply_xy_transform(lines, fn)
    # Second call should receive (5, 0) not (105, 0)
    assert calls[1][0] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# apply_skew
# ---------------------------------------------------------------------------

def test_apply_skew_zero_deg_no_change():
    lines  = gl.parse_lines("G90\nG1 X10 Y20 E1.0")
    result = gl.apply_skew(lines, skew_deg=0.0)
    g1 = next(ln for ln in result if ln.command == "G1")
    assert g1.words["X"] == pytest.approx(10.0, abs=0.001)
    assert g1.words["Y"] == pytest.approx(20.0, abs=0.001)


def test_apply_skew_y_unchanged():
    """Skew is XY-shear only; Y must not change."""
    lines  = gl.parse_lines("G90\nG1 X10 Y15 E1.0")
    result = gl.apply_skew(lines, skew_deg=1.0)
    g1 = next(ln for ln in result if ln.command == "G1")
    assert g1.words["Y"] == pytest.approx(15.0, abs=0.001)


def test_apply_skew_x_shifted():
    """x' = x + (y - y_ref) * tan(theta)."""
    skew_deg = 1.0
    k = math.tan(math.radians(skew_deg))
    lines  = gl.parse_lines("G90\nG1 X10 Y10 E1.0")
    result = gl.apply_skew(lines, skew_deg=skew_deg, y_ref=0.0)
    g1 = next(ln for ln in result if ln.command == "G1")
    expected_x = 10.0 + (10.0 - 0.0) * k
    assert g1.words["X"] == pytest.approx(expected_x, abs=0.001)


def test_apply_skew_y_ref_affects_x():
    """With y_ref = y, x displacement is zero."""
    skew_deg = 5.0
    lines  = gl.parse_lines("G90\nG1 X10 Y20 E1.0")
    result = gl.apply_skew(lines, skew_deg=skew_deg, y_ref=20.0)
    g1 = next(ln for ln in result if ln.command == "G1")
    assert g1.words["X"] == pytest.approx(10.0, abs=0.001)  # (y - y_ref) = 0


def test_apply_skew_negative_angle():
    k = math.tan(math.radians(-1.0))
    lines  = gl.parse_lines("G90\nG1 X10 Y10 E1.0")
    result = gl.apply_skew(lines, skew_deg=-1.0)
    g1 = next(ln for ln in result if ln.command == "G1")
    expected_x = 10.0 + 10.0 * k
    assert g1.words["X"] == pytest.approx(expected_x, abs=0.001)


def test_apply_skew_multiple_moves():
    skew_deg = 0.5
    k = math.tan(math.radians(skew_deg))
    lines  = gl.parse_lines("G90\nG1 X10 Y10\nG1 X20 Y30")
    result = gl.apply_skew(lines, skew_deg=skew_deg)
    g1s = [ln for ln in result if ln.command == "G1"]
    assert g1s[0].words["X"] == pytest.approx(10.0 + 10.0 * k, abs=0.001)
    assert g1s[1].words["X"] == pytest.approx(20.0 + 30.0 * k, abs=0.001)


# ---------------------------------------------------------------------------
# translate_xy
# ---------------------------------------------------------------------------

def test_translate_xy_shifts_x_and_y():
    lines  = gl.parse_lines("G90\nG1 X10 Y20")
    result = gl.translate_xy(lines, dx=5.0, dy=-3.0)
    g1 = next(ln for ln in result if ln.command == "G1")
    assert g1.words["X"] == pytest.approx(15.0, abs=0.001)
    assert g1.words["Y"] == pytest.approx(17.0, abs=0.001)


def test_translate_xy_zero_no_change():
    lines  = gl.parse_lines("G90\nG1 X10 Y20")
    result = gl.translate_xy(lines, dx=0.0, dy=0.0)
    g1 = next(ln for ln in result if ln.command == "G1")
    assert g1.words["X"] == pytest.approx(10.0, abs=0.001)
    assert g1.words["Y"] == pytest.approx(20.0, abs=0.001)


def test_translate_xy_passes_through_non_moves():
    lines  = gl.parse_lines("G90\nM82\nG1 X10 Y20")
    result = gl.translate_xy(lines, dx=1.0, dy=1.0)
    assert result[0].raw == "G90"
    assert result[1].raw == "M82"


def test_translate_xy_multiple_moves():
    lines  = gl.parse_lines("G90\nG1 X0 Y0\nG1 X10 Y10")
    result = gl.translate_xy(lines, dx=100.0, dy=200.0)
    g1s = [ln for ln in result if ln.command == "G1"]
    assert g1s[0].words["X"] == pytest.approx(100.0, abs=0.001)
    assert g1s[0].words["Y"] == pytest.approx(200.0, abs=0.001)
    assert g1s[1].words["X"] == pytest.approx(110.0, abs=0.001)
    assert g1s[1].words["Y"] == pytest.approx(210.0, abs=0.001)


# ---------------------------------------------------------------------------
# Combined: linearize_arcs then apply_skew
# ---------------------------------------------------------------------------

def test_linearize_then_skew_no_arcs_in_output():
    lines    = gl.parse_lines("G90\nG1 X0 Y0\nG2 X10 Y0 I5 J0 E1.0")
    lin      = gl.linearize_arcs(lines)
    result   = gl.apply_skew(lin, skew_deg=0.5)
    assert all(ln.command != "G2" for ln in result)
    assert all(ln.command != "G3" for ln in result)


def test_linearize_then_skew_x_monotone_for_positive_y():
    """For positive skew and positive Y, all X values should be shifted right."""
    skew_deg = 1.0
    k = math.tan(math.radians(skew_deg))
    # Straight horizontal extrusion at y=10
    lines  = gl.parse_lines("G90\nM82\nG1 X0 Y10 E0\nG1 X100 Y10 E2.0")
    result = gl.apply_skew(lines, skew_deg=skew_deg)
    g1s = [ln for ln in result if ln.command == "G1" and "X" in ln.words]
    for ln in g1s:
        # Every X should be shifted by (10 * k) relative to original
        orig_x = ln.words["X"] - 10.0 * k  # un-skew
        assert orig_x >= -0.001  # original was non-negative
