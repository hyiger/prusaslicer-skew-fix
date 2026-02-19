import math


def test_analyze_no_xy_moves_reports_empty(tmp_path, load_module):
    m = load_module
    g = tmp_path / "no_xy.gcode"
    g.write_text("\n".join([
        "; comment only",
        "M82",
        "G1 E1.0",
        ""
    ]), encoding="utf-8")

    lines = m.analyze_gcode(
        str(g),
        k=math.tan(math.radians(-0.15)),
        y_ref=0.0,
        bed_x_min=0.0, bed_x_max=250.0,
        bed_y_min=0.0, bed_y_max=220.0,
        recenter=False,
        margin=0.0,
        recenter_mode="center",
    )
    assert any("no XY moves found to analyze." in ln for ln in lines)


def test_analyze_skips_relative_xy_until_absolute(tmp_path, load_module):
    m = load_module
    g = tmp_path / "relative_then_absolute.gcode"
    g.write_text("\n".join([
        "G91",
        "G1 X10 Y0",
        "G90",
        "G1 X5 Y7",
        ""
    ]), encoding="utf-8")

    lines = m.analyze_gcode(
        str(g),
        k=0.0,
        y_ref=0.0,
        bed_x_min=0.0, bed_x_max=250.0,
        bed_y_min=0.0, bed_y_max=220.0,
        recenter=False,
        margin=0.0,
        recenter_mode="center",
    )
    # Relative move is skipped; only the absolute move is analyzed.
    assert any("all-move bounds (pre):  X[5.000,5.000] Y[7.000,7.000]" in ln for ln in lines)
