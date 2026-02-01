
import math
import subprocess
import sys
from pathlib import Path

from skew_fix_ps import skew_deg_from_square, skew_deg_from_rectangle

SCRIPT = str(Path(__file__).resolve().parents[1] / "skew_fix_ps.py")

def _run(args):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)

def test_skew_deg_from_square_zero():
    deg = skew_deg_from_square("141.421,141.421,100")
    assert abs(deg) < 1e-9

def test_skew_deg_from_square_known():
    # AD=100, tan(theta)=0.002 -> AC-BD=0.4
    deg = skew_deg_from_square("141.821,141.421,100")
    assert abs(deg - math.degrees(math.atan(0.002))) < 1e-9

def test_skew_deg_from_rectangle_known():
    # AD=80, tan(theta)=0.00625 -> AC-BD=1.0
    deg = skew_deg_from_rectangle("180.5,179.5,80,120")
    assert abs(deg - math.degrees(math.atan(0.00625))) < 1e-9

def test_cli_rejects_missing_skew(tmp_path):
    g = tmp_path / "t.gcode"
    g.write_text("G90\nG1 X0 Y0\n", encoding="utf-8")
    r = _run([str(g)])
    assert r.returncode != 0
    assert "Specify exactly one" in (r.stderr + r.stdout)

def test_cli_skew_from_square_writes_header(tmp_path):
    g = tmp_path / "t.gcode"
    g.write_text("G90\nG1 X10 Y10 E1\n", encoding="utf-8")
    r = _run(["--skew-from-square", "141.821,141.421,100", str(g)])
    assert r.returncode == 0, r.stderr + r.stdout
    txt = g.read_text(encoding="utf-8")
    assert "; prusaslicer-skew-fix: applied XY skew correction" in txt
    # header includes derived skew_deg
    assert any("skew_deg=" in ln for ln in txt.splitlines()[:5])
