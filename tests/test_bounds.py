from pathlib import Path
import pytest

def test_inbed_extruding_bounds_ignore_out_of_bed_and_nonextruding(tmp_path, load_module):
    m = load_module
    g = tmp_path / "t.gcode"
    # Purge move outside bed with extrusion should be ignored because endpoints out-of-bed.
    # Travel outside bed without extrusion should also be ignored.
    g.write_text("\n".join([
        "G90",
        "M82",
        "G1 X-10 Y0 F6000",          # travel out of bed
        "G1 X-10 Y0 E1.0 F300",      # extrude out of bed (ignored)
        "G1 X10 Y10 F6000",          # travel in bed
        "G1 X20 Y20 E1.5 F1200",     # extrude in bed
        "G1 X30 Y40 E2.0",           # extrude in bed
        ""
    ]), encoding="utf-8")

    minx, maxx, miny, maxy = m.compute_inbed_extruding_bounds_original(
        str(g),
        bed_x_min=0.0, bed_x_max=250.0,
        bed_y_min=0.0, bed_y_max=220.0,
    )
    assert minx == pytest.approx(20.0)
    assert maxx == pytest.approx(30.0)
    assert miny == pytest.approx(20.0)
    assert maxy == pytest.approx(40.0)

def test_recenter_mode_center_vs_clamp_translation_choice(tmp_path, load_module):
    m = load_module
    g = tmp_path / "t.gcode"
    g.write_text("\n".join([
        "G90",
        "M82",
        "G1 X10 Y10 E1.0",
        "G1 X20 Y20 E2.0",
        ""
    ]), encoding="utf-8")

    dx_c, dy_c, _ = m.compute_translation_for_bounds(
        str(g),
        k=0.0,
        y_ref=0.0,
        bed_x_min=0.0, bed_x_max=100.0,
        bed_y_min=0.0, bed_y_max=100.0,
        margin=0.0,
        recenter_mode="center",
    )
    dx_k, dy_k, _ = m.compute_translation_for_bounds(
        str(g),
        k=0.0,
        y_ref=0.0,
        bed_x_min=0.0, bed_x_max=100.0,
        bed_y_min=0.0, bed_y_max=100.0,
        margin=0.0,
        recenter_mode="clamp",
    )

    assert dx_c == pytest.approx(35.0)
    assert dy_c == pytest.approx(35.0)
    assert dx_k == pytest.approx(0.0)
    assert dy_k == pytest.approx(0.0)

def test_recenter_raises_when_model_cannot_fit(tmp_path, load_module):
    m = load_module
    g = tmp_path / "too_wide.gcode"
    g.write_text("\n".join([
        "G90",
        "M82",
        "G1 X0 Y0 E1.0",
        "G1 X20 Y0 E2.0",
        ""
    ]), encoding="utf-8")

    with pytest.raises(SystemExit, match="cannot fit within bed"):
        m.compute_translation_for_bounds(
            str(g),
            k=0.0,
            y_ref=0.0,
            bed_x_min=0.0, bed_x_max=20.0,
            bed_y_min=0.0, bed_y_max=20.0,
            margin=15.0,
            recenter_mode="clamp",
        )

def test_recenter_bounds_ignore_travel_arcs(tmp_path, load_module):
    m = load_module
    g = tmp_path / "arc_travel.gcode"
    g.write_text("\n".join([
        "G90",
        "M82",
        "G0 X0 Y10",
        # Travel-only arc (no E). Current bounds logic includes arc geometry.
        "G2 X20 Y10 I10 J0",
        ""
    ]), encoding="utf-8")

    _, _, bounds = m.compute_translation_for_bounds(
        str(g),
        k=0.0,
        y_ref=0.0,
        bed_x_min=0.0, bed_x_max=250.0,
        bed_y_min=0.0, bed_y_max=220.0,
        margin=0.0,
        recenter_mode="clamp",
    )
    minx, maxx, miny, maxy = bounds
    # Travel-only arc should not affect model/extruding bounds.
    assert minx == 0.0
    assert maxx == 0.0
    assert miny == 0.0
    assert maxy == 0.0
