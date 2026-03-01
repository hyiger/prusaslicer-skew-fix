"""Tests for gcode_lib modal state tracking: advance_state, iter_with_state, iter_* helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gcode_lib as gl


def _parse(text: str) -> list[gl.GCodeLine]:
    return gl.parse_lines(text)


def _state_after(text: str) -> gl.ModalState:
    """Return the modal state after processing all lines in *text*."""
    state = gl.ModalState()
    for line in gl.parse_lines(text):
        gl.advance_state(state, line)
    return state


# ---------------------------------------------------------------------------
# advance_state — modal flags
# ---------------------------------------------------------------------------

def test_advance_state_g90_sets_abs_xy():
    st = gl.ModalState(abs_xy=False)
    gl.advance_state(st, gl.parse_line("G90"))
    assert st.abs_xy is True


def test_advance_state_g91_clears_abs_xy():
    st = gl.ModalState(abs_xy=True)
    gl.advance_state(st, gl.parse_line("G91"))
    assert st.abs_xy is False


def test_advance_state_m82_sets_abs_e():
    st = gl.ModalState(abs_e=False)
    gl.advance_state(st, gl.parse_line("M82"))
    assert st.abs_e is True


def test_advance_state_m83_clears_abs_e():
    st = gl.ModalState(abs_e=True)
    gl.advance_state(st, gl.parse_line("M83"))
    assert st.abs_e is False


def test_advance_state_g901_sets_ij_absolute():
    st = gl.ModalState(ij_relative=True)
    gl.advance_state(st, gl.parse_line("G90.1"))
    assert st.ij_relative is False


def test_advance_state_g911_sets_ij_relative():
    st = gl.ModalState(ij_relative=False)
    gl.advance_state(st, gl.parse_line("G91.1"))
    assert st.ij_relative is True


# ---------------------------------------------------------------------------
# advance_state — absolute G0/G1 moves
# ---------------------------------------------------------------------------

def test_advance_state_g1_updates_xy():
    st = _state_after("G90\nG1 X10 Y20")
    assert st.x == pytest.approx(10.0)
    assert st.y == pytest.approx(20.0)


def test_advance_state_g1_updates_z():
    st = _state_after("G90\nG1 Z0.2")
    assert st.z == pytest.approx(0.2)


def test_advance_state_g0_travel_updates_position():
    st = _state_after("G90\nG0 X50 Y60")
    assert st.x == pytest.approx(50.0)
    assert st.y == pytest.approx(60.0)


def test_advance_state_g1_partial_x_only():
    st = _state_after("G90\nG1 X5 Y10\nG1 X20")
    assert st.x == pytest.approx(20.0)
    assert st.y == pytest.approx(10.0)  # y unchanged


def test_advance_state_g1_partial_y_only():
    st = _state_after("G90\nG1 X5 Y10\nG1 Y30")
    assert st.x == pytest.approx(5.0)
    assert st.y == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# advance_state — relative G0/G1 moves
# ---------------------------------------------------------------------------

def test_advance_state_relative_xy():
    st = _state_after("G90\nG1 X10 Y10\nG91\nG1 X5 Y-3")
    assert st.x == pytest.approx(15.0)
    assert st.y == pytest.approx(7.0)


def test_advance_state_relative_z():
    st = _state_after("G91\nG1 Z0.2\nG1 Z0.2")
    assert st.z == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# advance_state — E tracking
# ---------------------------------------------------------------------------

def test_advance_state_abs_e():
    st = _state_after("M82\nG1 X0 Y0 E1.5")
    assert st.e == pytest.approx(1.5)


def test_advance_state_rel_e_accumulates():
    st = _state_after("M83\nG1 X0 Y0 E0.5\nG1 X1 Y0 E0.5")
    assert st.e == pytest.approx(1.0)


def test_advance_state_f_updated():
    st = _state_after("G1 X0 Y0 F3000")
    assert st.f == pytest.approx(3000.0)


# ---------------------------------------------------------------------------
# advance_state — G92 position reset
# ---------------------------------------------------------------------------

def test_advance_state_g92_e_reset():
    st = _state_after("M82\nG1 E5.0\nG92 E0")
    assert st.e == pytest.approx(0.0)


def test_advance_state_g92_xy_reset():
    st = _state_after("G90\nG1 X50 Y60\nG92 X0 Y0")
    assert st.x == pytest.approx(0.0)
    assert st.y == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# advance_state — arcs update endpoint
# ---------------------------------------------------------------------------

def test_advance_state_arc_endpoint_abs():
    st = _state_after("G90\nG1 X0 Y0\nG2 X10 Y0 I5 J0")
    assert st.x == pytest.approx(10.0)
    assert st.y == pytest.approx(0.0)


def test_advance_state_arc_feedrate():
    st = _state_after("G90\nG2 X10 Y0 I5 J0 F1500")
    assert st.f == pytest.approx(1500.0)


# ---------------------------------------------------------------------------
# advance_state — non-modal lines with E word (e.g. firmware reset)
# ---------------------------------------------------------------------------

def test_advance_state_non_motion_e_word():
    # A bare line with only an E word (not G0/G1/G2/G3) still advances state.e
    st = _state_after("M83\nT0 E1.0")
    # E word should be processed (relative mode)
    assert st.e == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# ModalState.copy()
# ---------------------------------------------------------------------------

def test_modal_state_copy_is_independent():
    original = gl.ModalState(x=5.0, y=10.0, abs_xy=True)
    copy = original.copy()
    copy.x = 99.0
    copy.abs_xy = False
    assert original.x == pytest.approx(5.0)
    assert original.abs_xy is True


# ---------------------------------------------------------------------------
# iter_with_state
# ---------------------------------------------------------------------------

def test_iter_with_state_yields_all_lines():
    lines = gl.parse_lines("G90\nG1 X10\nG1 X20")
    pairs = list(gl.iter_with_state(lines))
    assert len(pairs) == 3


def test_iter_with_state_state_before_line():
    """State yielded should reflect the state BEFORE the line is processed."""
    lines = gl.parse_lines("G90\nG1 X10 Y0\nG1 X20 Y0")
    pairs = list(gl.iter_with_state(lines))

    # Before the second G1: state.x should be 10 (set by first G1)
    _, st_before_third = pairs[2]
    assert st_before_third.x == pytest.approx(10.0)


def test_iter_with_state_initial_state_respected():
    lines = gl.parse_lines("G1 X5 Y5")
    init  = gl.ModalState(x=100.0, y=100.0)
    pairs = list(gl.iter_with_state(lines, initial_state=init))
    _, st = pairs[0]
    assert st.x == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# iter_moves
# ---------------------------------------------------------------------------

def test_iter_moves_filters_correctly():
    lines = gl.parse_lines("G90\nM82\nG1 X10\nG2 X20 I5 J0\nG1 X30")
    moves = list(gl.iter_moves(lines))
    assert len(moves) == 2  # G1 X10 and G1 X30


def test_iter_moves_empty_when_no_moves():
    lines = gl.parse_lines("G90\nM82\n; comment")
    assert list(gl.iter_moves(lines)) == []


# ---------------------------------------------------------------------------
# iter_arcs
# ---------------------------------------------------------------------------

def test_iter_arcs_filters_correctly():
    lines = gl.parse_lines("G90\nG1 X0 Y0\nG2 X10 Y0 I5 J0\nG3 X5 Y5 I-2 J2\nG1 X20")
    arcs = list(gl.iter_arcs(lines))
    assert len(arcs) == 2
    assert all(ln.is_arc for ln, _ in arcs)


# ---------------------------------------------------------------------------
# iter_extruding
# ---------------------------------------------------------------------------

def test_iter_extruding_abs_e():
    lines = gl.parse_lines("M82\nG1 X5 Y0 E1.0\nG1 X10 Y0 E2.0\nG1 X15 Y0")
    ext = list(gl.iter_extruding(lines))
    assert len(ext) == 2  # Both have positive E delta


def test_iter_extruding_rel_e():
    lines = gl.parse_lines("M83\nG1 X5 E0.5\nG1 X10 E-0.1\nG1 X15 E0.3")
    ext = list(gl.iter_extruding(lines))
    assert len(ext) == 2  # E0.5 and E0.3 are positive


def test_iter_extruding_no_e_word_excluded():
    lines = gl.parse_lines("M82\nG1 X5 Y0\nG1 X10 Y0")
    ext = list(gl.iter_extruding(lines))
    assert len(ext) == 0


def test_iter_extruding_retraction_excluded():
    lines = gl.parse_lines("M82\nG1 X0 Y0 E1.0\nG1 X0 Y0 E0.5")  # E decreasing
    ext = list(gl.iter_extruding(lines))
    assert len(ext) == 1
