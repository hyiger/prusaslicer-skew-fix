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
