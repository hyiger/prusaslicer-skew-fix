import re
import tempfile
import shutil
from pathlib import Path

from skew_fix_ps import rewrite

def _run(gcode: str) -> str:
    d = tempfile.mkdtemp()
    try:
        p = Path(d) / "in.gcode"
        p.write_text(gcode, encoding="utf-8")
        # Use fixed y_ref and no recenter so output is deterministic for assertions.
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
            analyze_only=False,
        )
        return p.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(d)

def test_no_g2_g3_survive():
    out = _run("""G90
G1 X0 Y0
G2 X50 Y50 I25 J0 E5
G3 X0 Y0 I-25 J0
""")
    assert "G2" not in out
    assert "G3" not in out

def test_arc_to_move_state_continuity():
    out = _run("""G90
G1 X0 Y0
G2 X50 Y0 I25 J0 E5
G1 X60 Y10 E6
""")
    g1s = [l for l in out.splitlines() if l.startswith("G1")]
    assert any("X60" in l and "Y10" in l for l in g1s), "Final move not present/rewritten"

def test_relative_e_with_arc_sums_exactly():
    out = _run("""G90
M83
G1 X0 Y0
G2 X40 Y0 I20 J0 E2
""")
    e_vals = []
    for l in out.splitlines():
        if l.startswith("G1") and "E" in l:
            m = re.search(r"E([0-9.+-]+)", l)
            if m:
                e_vals.append(float(m.group(1)))
    assert e_vals, "No extrusion values emitted"
    assert abs(sum(e_vals) - 2.0) < 1e-6

def test_arc_feedrate_and_comment_preserved_on_first_segment_only():
    out = _run("""G90
G1 X0 Y0
G2 X20 Y0 I10 J0 F1200 ; arc comment
""")
    g1s = [l for l in out.splitlines() if l.startswith("G1")]
    assert g1s, "No G1 output produced"
    comment_lines = [l for l in g1s if "arc comment" in l]
    assert len(comment_lines) == 1
    assert "F1200" in comment_lines[0]
    assert all("F1200" not in l for l in g1s if l is not comment_lines[0])
    assert all("arc comment" not in l for l in g1s if l is not comment_lines[0])
