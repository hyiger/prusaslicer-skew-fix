import tempfile
import shutil
from pathlib import Path
from skew_fix_ps import rewrite

def _run(gcode: str, analyze_only=False) -> str:
    d = tempfile.mkdtemp()
    try:
        p = Path(d) / "in.gcode"
        p.write_text(gcode, encoding="utf-8")
        rewrite(
            str(p),
            skew_deg=0.15,
            recenter=False,
            bed_x_min=0,
            bed_x_max=250,
            bed_y_min=0,
            bed_y_max=210,
            margin=0,
            recenter_mode="clamp",
            shear_y_ref_mode="fixed",
            shear_y_ref=0.0,
            analyze_only=analyze_only,
        )
        return p.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(d)

def test_travel_arc_no_extrusion():
    out = _run("G90\nG2 X50 Y0 I25 J0\n")
    for l in out.splitlines():
        if l.startswith("G1"):
            assert "E" not in l

def test_arc_followed_by_arc():
    out = _run("G90\nG2 X50 Y0 I25 J0\nG3 X0 Y0 I-25 J0\n")
    assert "G2" not in out and "G3" not in out

def test_analyze_only_produces_no_rewrite():
    out = _run("G90\nG2 X50 Y0 I25 J0\n", analyze_only=True)
    assert "G2" in out

def test_full_circle_arc_linearizes_to_many_segments():
    # G2 with no X/Y and a nonzero I/J is a full circle (start == end).
    # Previously this collapsed to a single degenerate point.
    out = _run(
        "G90\n"
        "M82\n"
        "G0 X10 Y0\n"
        "G2 I-10 J0 E1.0\n"  # full CW circle of radius 10, center at (0,0)
    )
    lines = out.splitlines()
    g1s = [ln for ln in lines if ln.startswith("G1") and "E" in ln]
    assert "G2" not in out and "G3" not in out
    assert len(g1s) > 10


def test_near_full_sweep_arc_linearizes_to_many_segments_and_keeps_following_move():
    out = _run(
        "G90\n"
        "M82\n"
        "G0 X1 Y0\n"
        # Slightly positive end angle with CW forces a near-360 deg sweep.
        "G2 X0.999999 Y0.001 I-1 J0 E1.0\n"
        "G1 X2 Y0 E2.0\n"
    )
    lines = out.splitlines()
    g1s = [ln for ln in lines if ln.startswith("G1")]
    assert "G2" not in out and "G3" not in out
    assert len(g1s) > 10
    assert any("X2" in ln and "Y0" in ln for ln in g1s)
