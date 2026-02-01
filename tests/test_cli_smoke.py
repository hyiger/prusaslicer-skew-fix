
import subprocess
import sys
from pathlib import Path

def test_cli_smoke(tmp_path):
    g = tmp_path / "t.gcode"
    g.write_text("G90\nG1 X0 Y0\n", encoding="utf-8")

    subprocess.check_call(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "skew_fix_ps.py"),
            "--skew-deg",
            "0.0",
            str(g),
        ]
    )
