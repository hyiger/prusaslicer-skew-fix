"""Tests for gcode_lib parsing utilities: split_comment, parse_words, parse_line, parse_lines."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gcode_lib as gl


# ---------------------------------------------------------------------------
# split_comment
# ---------------------------------------------------------------------------

def test_split_comment_with_comment():
    code, comment = gl.split_comment("G1 X10 Y20 ; move")
    assert code == "G1 X10 Y20"
    assert comment == "; move"


def test_split_comment_no_comment():
    code, comment = gl.split_comment("G1 X10 Y20")
    assert code == "G1 X10 Y20"
    assert comment == ""


def test_split_comment_only_comment():
    code, comment = gl.split_comment("; this is a comment")
    assert code == ""
    assert comment == "; this is a comment"


def test_split_comment_multiple_semicolons():
    code, comment = gl.split_comment("G1 X10 ; first ; second")
    assert code == "G1 X10"
    assert comment == "; first ; second"


def test_split_comment_strips_trailing_space_from_code():
    code, _ = gl.split_comment("G1 X10   ; comment")
    assert code == "G1 X10"


def test_split_comment_empty_line():
    code, comment = gl.split_comment("")
    assert code == ""
    assert comment == ""


def test_split_comment_blank_line():
    code, comment = gl.split_comment("   ")
    assert code == ""
    assert comment == ""


# ---------------------------------------------------------------------------
# parse_words
# ---------------------------------------------------------------------------

def test_parse_words_basic():
    d = gl.parse_words("G1 X10.5 Y-3 E0.1")
    assert d["X"] == pytest.approx(10.5)
    assert d["Y"] == pytest.approx(-3.0)
    assert d["E"] == pytest.approx(0.1)


def test_parse_words_keys_are_uppercase():
    d = gl.parse_words("g1 x5 y10")
    assert "X" in d
    assert "Y" in d


def test_parse_words_scientific_notation():
    d = gl.parse_words("G1 E1.5e-3")
    assert d["E"] == pytest.approx(1.5e-3)


def test_parse_words_with_ij():
    d = gl.parse_words("G2 X10 Y0 I5 J0")
    assert d["I"] == pytest.approx(5.0)
    assert d["J"] == pytest.approx(0.0)


def test_parse_words_feedrate():
    d = gl.parse_words("G1 X10 F3000")
    assert d["F"] == pytest.approx(3000.0)


def test_parse_words_empty():
    assert gl.parse_words("") == {}
    assert gl.parse_words("; comment only") == {}


def test_parse_words_no_axis_words():
    assert gl.parse_words("G28") == {}


def test_parse_words_all_axes():
    d = gl.parse_words("G2 X1 Y2 Z3 E4 F5 I6 J7 K8 R9")
    for axis in ("X", "Y", "Z", "E", "F", "I", "J", "K", "R"):
        assert axis in d


# ---------------------------------------------------------------------------
# parse_line
# ---------------------------------------------------------------------------

def test_parse_line_move():
    line = gl.parse_line("G1 X10 Y20 E1.0 ; extrude")
    assert line.command == "G1"
    assert line.words["X"] == pytest.approx(10.0)
    assert line.words["Y"] == pytest.approx(20.0)
    assert line.words["E"] == pytest.approx(1.0)
    assert line.comment == "; extrude"
    assert line.raw == "G1 X10 Y20 E1.0 ; extrude"


def test_parse_line_arc():
    line = gl.parse_line("G2 X5 Y5 I2 J0")
    assert line.command == "G2"
    assert line.is_arc


def test_parse_line_modal():
    line = gl.parse_line("G90")
    assert line.command == "G90"
    assert not line.is_move
    assert not line.is_arc


def test_parse_line_lowercase_normalised():
    line = gl.parse_line("g1 x5 y10")
    assert line.command == "G1"
    assert line.is_move


def test_parse_line_blank():
    line = gl.parse_line("")
    assert line.command == ""
    assert line.words == {}
    assert line.comment == ""
    assert line.is_blank


def test_parse_line_comment_only():
    line = gl.parse_line("; just a comment")
    assert line.command == ""
    assert line.comment == "; just a comment"


def test_parse_line_strips_trailing_newline():
    line = gl.parse_line("G1 X10\n")
    assert line.raw == "G1 X10"


def test_parse_line_is_move_g0():
    assert gl.parse_line("G0 X10 Y20").is_move


def test_parse_line_is_move_g1():
    assert gl.parse_line("G1 X10 Y20 E0.5").is_move


def test_parse_line_is_arc_g2():
    assert gl.parse_line("G2 X5 Y5 I2 J0").is_arc


def test_parse_line_is_arc_g3():
    assert gl.parse_line("G3 X5 Y5 I-2 J0").is_arc


def test_parse_line_is_not_move_arc():
    assert not gl.parse_line("G2 X5 Y5 I2 J0").is_move
    assert not gl.parse_line("G1 X5 Y5").is_arc


# ---------------------------------------------------------------------------
# parse_lines
# ---------------------------------------------------------------------------

def test_parse_lines_count():
    text = "G90\nM82\nG1 X10 Y20 E1.0\n"
    lines = gl.parse_lines(text)
    assert len(lines) == 3


def test_parse_lines_empty_string():
    assert gl.parse_lines("") == []


def test_parse_lines_preserves_order():
    text = "G90\nM82\nG1 X10\nG1 X20"
    lines = gl.parse_lines(text)
    assert lines[0].command == "G90"
    assert lines[1].command == "M82"
    assert lines[2].command == "G1"
    assert lines[3].command == "G1"
    assert lines[2].words["X"] == pytest.approx(10.0)
    assert lines[3].words["X"] == pytest.approx(20.0)


def test_parse_lines_blank_lines_preserved():
    text = "G90\n\nG1 X10"
    lines = gl.parse_lines(text)
    assert len(lines) == 3
    assert lines[1].is_blank
