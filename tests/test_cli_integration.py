import subprocess
import sys
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parents[1] / "skew_fix_ps.py")

def _run(args):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)

def test_cli_smoke_basic(tmp_path):
    g = tmp_path / "t.gcode"
    g.write_text("G90\nG1 X0 Y0\n", encoding="utf-8")
    r = _run(["--skew-deg", "0.0", str(g)])
    assert r.returncode == 0, r.stderr + r.stdout
    assert "G1" in g.read_text(encoding="utf-8")

def test_cli_analyze_only_does_not_modify(tmp_path):
    g = tmp_path / "t.gcode"
    original = "G90\nG1 X10 Y10 F1200\n"
    g.write_text(original, encoding="utf-8")
    r = _run(["--skew-deg", "0.15", "--analyze-only", str(g)])
    assert r.returncode == 0, r.stderr + r.stdout
    assert g.read_text(encoding="utf-8") == original

def test_cli_empty_file_ok(tmp_path):
    g = tmp_path / "empty.gcode"
    g.write_text("", encoding="utf-8")
    r = _run(["--skew-deg", "0.0", str(g)])
    assert r.returncode == 0, r.stderr + r.stdout

def test_cli_comment_only_ok(tmp_path):
    g = tmp_path / "comments.gcode"
    g.write_text("; header\n; only comments\n", encoding="utf-8")
    r = _run(["--skew-deg", "0.0", str(g)])
    assert r.returncode == 0, r.stderr + r.stdout

def test_cli_single_line_move_ok(tmp_path):
    g = tmp_path / "single.gcode"
    g.write_text("G90\nG1 X5 Y5\n", encoding="utf-8")
    r = _run(["--skew-deg", "-0.15", "--recenter-to-bed", "--recenter-mode", "clamp", str(g)])
    assert r.returncode == 0, r.stderr + r.stdout
