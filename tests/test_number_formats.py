import math
import re

import pytest


def _motion_with_e1(lines):
    return next((ln for ln in lines if ln.startswith("G1") and re.search(r"\bE1(?:\.0+)?\b", ln)), None)


def test_plus_prefixed_xy_values_are_rewritten(tmp_path, load_module):
    m = load_module
    k = math.tan(math.radians(-0.15))
    g = tmp_path / "plus.gcode"
    g.write_text("G90\nM82\nG1 X+10 Y+20 E1\n", encoding="utf-8")

    m.rewrite(
        str(g),
        skew_deg=-0.15,
        recenter=False,
        bed_x_min=0.0, bed_x_max=250.0, bed_y_min=0.0, bed_y_max=220.0, margin=0.0,
        recenter_mode="center",
        shear_y_ref_mode="fixed", shear_y_ref=0.0,
        analyze_only=False,
    )

    line = _motion_with_e1(g.read_text(encoding="utf-8").splitlines())
    assert line is not None
    mx = re.search(r"\bX([+-]?\d+(?:\.\d+)?)\b", line)
    my = re.search(r"\bY([+-]?\d+(?:\.\d+)?)\b", line)
    assert mx and my
    assert float(my.group(1)) == pytest.approx(20.0)
    assert float(mx.group(1)) == pytest.approx(10.0 + 20.0 * k, abs=1e-3)


def test_scientific_notation_xy_values_are_replaced_not_appended(tmp_path, load_module):
    m = load_module
    g = tmp_path / "sci.gcode"
    g.write_text("G90\nM82\nG1 X1e1 Y2e1 E1\n", encoding="utf-8")

    m.rewrite(
        str(g),
        skew_deg=-0.15,
        recenter=False,
        bed_x_min=0.0, bed_x_max=250.0, bed_y_min=0.0, bed_y_max=220.0, margin=0.0,
        recenter_mode="center",
        shear_y_ref_mode="fixed", shear_y_ref=0.0,
        analyze_only=False,
    )

    line = _motion_with_e1(g.read_text(encoding="utf-8").splitlines())
    assert line is not None
    x_tokens = [tok for tok in line.split() if tok.startswith("X")]
    y_tokens = [tok for tok in line.split() if tok.startswith("Y")]
    assert len(x_tokens) == 1
    assert len(y_tokens) == 1


def test_checksum_style_move_line_is_rewritten_and_checksum_suffix_preserved(tmp_path, load_module):
    m = load_module
    g = tmp_path / "chk.gcode"
    g.write_text("G90\nM82\nG1 X10 Y10 E1*12\n", encoding="utf-8")
    m.rewrite(
        str(g),
        skew_deg=-0.15,
        recenter=False,
        bed_x_min=0.0, bed_x_max=250.0, bed_y_min=0.0, bed_y_max=220.0, margin=0.0,
        recenter_mode="center",
        shear_y_ref_mode="fixed", shear_y_ref=0.0,
        analyze_only=False,
    )
    out = g.read_text(encoding="utf-8")
    line = next(ln for ln in out.splitlines() if ln.startswith("G1 "))
    assert "*12" in line
    assert "X10 " not in line
