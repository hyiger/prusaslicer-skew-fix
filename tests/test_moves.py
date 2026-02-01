import math
import re
import pytest


def _extract_first_move_xy(line: str):
    """Extract X/Y from a single G0/G1 line. Returns (x, y) or None."""
    if not re.match(r"^(G0|G1)\b", line.strip(), re.IGNORECASE):
        return None
    mx = re.search(r"\bX\s*(-?\d+(?:\.\d*)?|-?\.\d+)", line, re.IGNORECASE)
    my = re.search(r"\bY\s*(-?\d+(?:\.\d*)?|-?\.\d+)", line, re.IGNORECASE)
    if not (mx and my):
        return None
    return float(mx.group(1)), float(my.group(1))


def test_y_only_move_emits_x_for_correct_shear(tmp_path, load_module):
    """If a line only specifies Y, the skewed endpoint still changes X (x' depends on y).

    The rewriter must therefore emit X as well, otherwise the toolpath is wrong.
    """
    m = load_module
    k = math.tan(math.radians(-0.15))

    g = tmp_path / "y_only.gcode"
    g.write_text(
        "\n".join(
            [
                "G90",
                "M82",
                "G1 X10 Y10 E1.0",
                # X omitted; endpoint is (10, 20)
                "G1 Y20 E2.0",
                "",
            ]
        ),
        encoding="utf-8",
    )

    m.rewrite(
        str(g),
        skew_deg=-0.15,
        linearize=False,
        arc_seg_mm=0.2,
        arc_max_deg=5.0,
        recenter=False,
        bed_x_min=0.0,
        bed_x_max=250.0,
        bed_y_min=0.0,
        bed_y_max=220.0,
        margin=0.0,
        recenter_mode="center",
        eps=0.01,
        shear_y_ref_mode="fixed",
        shear_y_ref=0.0,
        xy_decimals=5,
        other_decimals=5,
        analyze_only=False,
    )

    out = g.read_text(encoding="utf-8").splitlines()
    # Find the rewritten Y20 move (it should now include X and Y)
    y20 = next((ln for ln in out if re.search(r"\bY20\b", ln)), None)
    assert y20 is not None
    xy = _extract_first_move_xy(y20)
    assert xy is not None, y20
    x_out, y_out = xy
    assert y_out == pytest.approx(20.0)
    assert x_out == pytest.approx(10.0 + 20.0 * k, abs=1e-5)


def test_negative_coordinates_are_supported(tmp_path, load_module):
    m = load_module
    k = math.tan(math.radians(-0.15))

    g = tmp_path / "neg.gcode"
    g.write_text(
        "\n".join(
            [
                "G90",
                "M82",
                "G1 X-10 Y25 E1.0",
                "",
            ]
        ),
        encoding="utf-8",
    )

    m.rewrite(
        str(g),
        skew_deg=-0.15,
        linearize=False,
        arc_seg_mm=0.2,
        arc_max_deg=5.0,
        recenter=False,
        bed_x_min=0.0,
        bed_x_max=250.0,
        bed_y_min=0.0,
        bed_y_max=220.0,
        margin=0.0,
        recenter_mode="center",
        eps=0.01,
        shear_y_ref_mode="fixed",
        shear_y_ref=0.0,
        xy_decimals=5,
        other_decimals=5,
        analyze_only=False,
    )

    out = g.read_text(encoding="utf-8").splitlines()
    move = next((ln for ln in out if ln.strip().startswith("G1") and "E1.0" in ln), None)
    assert move is not None
    xy = _extract_first_move_xy(move)
    assert xy is not None
    x_out, y_out = xy
    assert y_out == pytest.approx(25.0)
    assert x_out == pytest.approx(-10.0 + 25.0 * k, abs=1e-5)


def test_extruder_only_moves_are_preserved(tmp_path, load_module):
    m = load_module

    g = tmp_path / "e_only.gcode"
    g.write_text(
        "\n".join(
            [
                "G90",
                "M82",
                "G1 E2.5 F300",
                "",
            ]
        ),
        encoding="utf-8",
    )

    m.rewrite(
        str(g),
        skew_deg=-0.15,
        linearize=False,
        arc_seg_mm=0.2,
        arc_max_deg=5.0,
        recenter=False,
        bed_x_min=0.0,
        bed_x_max=250.0,
        bed_y_min=0.0,
        bed_y_max=220.0,
        margin=0.0,
        recenter_mode="center",
        eps=0.01,
        shear_y_ref_mode="fixed",
        shear_y_ref=0.0,
        xy_decimals=5,
        other_decimals=5,
        analyze_only=False,
    )

    txt = g.read_text(encoding="utf-8")
    assert "G1 E2.5 F300" in txt


def test_zero_skew_preserves_endpoints(tmp_path, load_module):
    """Zero skew should not change XY endpoints (even if the rewriter adds missing axis words)."""
    m = load_module

    g = tmp_path / "zero.gcode"
    g.write_text(
        "\n".join(
            [
                "G90",
                "M82",
                "G1 X10 Y10 E1.0",
                "G1 X20 E2.0",  # Y implied
                "G1 Y30 E3.0",  # X implied
                "",
            ]
        ),
        encoding="utf-8",
    )

    m.rewrite(
        str(g),
        skew_deg=0.0,
        linearize=False,
        arc_seg_mm=0.2,
        arc_max_deg=5.0,
        recenter=False,
        bed_x_min=0.0,
        bed_x_max=250.0,
        bed_y_min=0.0,
        bed_y_max=220.0,
        margin=0.0,
        recenter_mode="center",
        eps=0.01,
        shear_y_ref_mode="fixed",
        shear_y_ref=0.0,
        xy_decimals=5,
        other_decimals=5,
        analyze_only=False,
    )

    out = [ln for ln in g.read_text(encoding="utf-8").splitlines() if ln.strip().startswith("G1")]
    # Find the three E moves and verify endpoints in order.
    moves = [ln for ln in out if re.search(r"\bE\s*", ln)]
    assert len(moves) >= 3

    # Expected endpoints in absolute XY.
    expected = [(10.0, 10.0), (20.0, 10.0), (20.0, 30.0)]
    for ln, (ex, ey) in zip(moves[-3:], expected):
        xy = _extract_first_move_xy(ln)
        assert xy is not None, ln
        x_out, y_out = xy
        assert x_out == pytest.approx(ex, abs=1e-5)
        assert y_out == pytest.approx(ey, abs=1e-5)
