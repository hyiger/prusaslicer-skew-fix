from pathlib import Path
import re
import math
import pytest

def _read_header_val(text: str, key: str):
    # Find e.g. "; prusaslicer-skew-fix: shear_y_ref_mode=auto  shear_y_ref=100.0000"
    for line in text.splitlines():
        if line.startswith("; prusaslicer-skew-fix:") and key in line:
            return line
    return None

def test_rewrite_auto_y_ref_uses_extruding_y_center(tmp_path, load_module):
    m = load_module
    g = tmp_path / "t.gcode"
    # Extruding moves Y from 10..110 => center 60
    g.write_text("\n".join([
        "G90",
        "M82",
        "G1 X10 Y10 E1.0",
        "G1 X20 Y110 E2.0",
        "G1 X30 Y60 E3.0",
        ""
    ]), encoding="utf-8")

    m.rewrite(
        str(g),
        skew_deg=-0.15,
        linearize=False, arc_seg_mm=0.2, arc_max_deg=5.0,
        recenter=False,
        bed_x_min=0.0, bed_x_max=250.0, bed_y_min=0.0, bed_y_max=220.0, margin=0.0,
        recenter_mode="center", eps=0.01,
        shear_y_ref_mode="auto", shear_y_ref=0.0,
        xy_decimals=5, other_decimals=5,
        analyze_only=False
    )

    txt = g.read_text(encoding="utf-8")
    line = _read_header_val(txt, "shear_y_ref_mode")
    assert line is not None
    assert "shear_y_ref_mode=auto" in line
    # numeric value in header is formatted to 4 decimals
    m2 = re.search(r"shear_y_ref=([0-9]+\.[0-9]+)", line)
    assert m2, line
    assert float(m2.group(1)) == pytest.approx(60.0, abs=1e-4)

def test_rewrite_linearize_arcs_emits_g1_only(tmp_path, load_module):
    m = load_module
    g = tmp_path / "arc.gcode"
    g.write_text("\n".join([
        "G90",
        "M82",
        "G0 X10 Y10",
        # CW quarter-circle around center (10,20) to (20,20)
        "G2 X20 Y20 I0 J10 E1.0 F1200",
        ""
    ]), encoding="utf-8")

    m.rewrite(
        str(g),
        skew_deg=-0.15,
        linearize=True, arc_seg_mm=5.0, arc_max_deg=90.0,  # coarse to keep few points
        recenter=False,
        bed_x_min=0.0, bed_x_max=250.0, bed_y_min=0.0, bed_y_max=220.0, margin=0.0,
        recenter_mode="center", eps=0.01,
        shear_y_ref_mode="fixed", shear_y_ref=0.0,
        xy_decimals=3, other_decimals=5,
        analyze_only=False
    )

    out = g.read_text(encoding="utf-8").splitlines()
    # Ensure original G2 is not present after rewrite when linearize=True
    assert not any(re.match(r"^G2\b", ln.strip(), re.IGNORECASE) for ln in out)
    # Should have at least one G1 generated from the arc
    assert any(re.match(r"^G1\b", ln.strip(), re.IGNORECASE) for ln in out)

def test_binary_guard_rejects_bgcode(tmp_path, load_module):
    m = load_module
    p = tmp_path / "bad.gcode"
    p.write_bytes(b"GCDE" + b"\x00"*100)
    with pytest.raises(SystemExit):
        m.rewrite(
            str(p),
            skew_deg=-0.15,
            linearize=False, arc_seg_mm=0.2, arc_max_deg=5.0,
            recenter=False,
            bed_x_min=0.0, bed_x_max=250.0, bed_y_min=0.0, bed_y_max=220.0, margin=0.0,
            recenter_mode="center", eps=0.01,
            shear_y_ref_mode="fixed", shear_y_ref=0.0,
            xy_decimals=3, other_decimals=5,
            analyze_only=False
        )
