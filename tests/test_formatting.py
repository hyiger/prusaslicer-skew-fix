import subprocess
import sys
import re
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parents[1] / "skew_fix_ps.py")

def _run(args):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)

def test_cli_decimal_formatting_applied(tmp_path):
    # Use values that force rounding differences and ensure the move is rewritten (non-zero skew).
    g = tmp_path / "t.gcode"
    g.write_text(
        "G90\n"
        "G1 X10 Y10 Z0.2 E0.1234567 F1500.123456\n",
        encoding="utf-8",
    )
    r = _run([
        "--skew-deg", "-0.15",
        "--shear-y-ref-mode", "fixed",
        "--shear-y-ref", "0",
        "--xy-decimals", "3",
        "--other-decimals", "5",
        str(g),
    ])
    assert r.returncode == 0, r.stderr

    out = g.read_text(encoding="utf-8")
    motion = next(line for line in out.splitlines() if line.startswith("G1 "))

    # X/Y should be rewritten and formatted to 3 decimals.
    assert re.search(r"\bX-?\d+\.\d{3}\b", motion)
    assert re.search(r"\bY-?\d+(?:\.\d+)?\b", motion)

    # Other axes should be formatted to 5 decimals.
    assert "E0.12346" in motion
    assert "F1500.12346" in motion
    assert "Z0.2" in motion

def test_z_value_unchanged_under_skew(tmp_path):
    g = tmp_path / "z.gcode"
    g.write_text(
        "G90\n"
        "G1 X10 Y10 Z0.2 E1.0\n",
        encoding="utf-8",
    )
    r = _run(["--skew-deg", "-0.15", "--shear-y-ref-mode", "fixed", "--shear-y-ref", "0", str(g)])
    assert r.returncode == 0, r.stderr

    out = g.read_text(encoding="utf-8")

    # Find the first motion line and ensure Z numeric value is preserved.
    motion = next(line for line in out.splitlines() if line.startswith("G1 "))
    z_token = next(tok for tok in motion.split() if tok.startswith("Z"))
    assert abs(float(z_token[1:]) - 0.2) < 1e-9
