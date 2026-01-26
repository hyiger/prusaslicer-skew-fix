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
        linearize=False,
        arc_seg_mm=0.2,
        arc_max_deg=5.0,
        bed_x_min=0.0, bed_x_max=250.0,
        bed_y_min=0.0, bed_y_max=220.0,
    )
    assert minx == pytest.approx(20.0)
    assert maxx == pytest.approx(30.0)
    assert miny == pytest.approx(20.0)
    assert maxy == pytest.approx(40.0)
