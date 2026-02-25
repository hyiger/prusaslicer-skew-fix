
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
    # Use exact geometry: for side L and skew k,
    # AC = L*sqrt((1+k)^2+1), BD = L*sqrt((k-1)^2+1)
    k = 0.002
    L = 100.0
    ac = L * math.sqrt((1 + k) ** 2 + 1)
    bd = L * math.sqrt((k - 1) ** 2 + 1)
    deg = skew_deg_from_square(f"{ac},{bd},{L}")
    assert abs(deg - math.degrees(math.atan(k))) < 1e-9

def test_skew_deg_from_rectangle_known():
    # Use exact geometry: for width W (AB), height H (AD) and skew k,
    # AC = sqrt((W+kH)^2+H^2), BD = sqrt((kH-W)^2+H^2)
    k = 0.00625
    W = 120.0  # AB
    H = 80.0   # AD
    ac = math.sqrt((W + k * H) ** 2 + H ** 2)
    bd = math.sqrt((k * H - W) ** 2 + H ** 2)
    deg = skew_deg_from_rectangle(f"{ac},{bd},{H},{W}")
    assert abs(deg - math.degrees(math.atan(k))) < 1e-9

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
