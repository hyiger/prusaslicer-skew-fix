
import pytest
import tempfile
import shutil
from pathlib import Path

from skew_fix_ps import rewrite

def test_relative_xy_is_rejected():
    d = tempfile.mkdtemp()
    try:
        p = Path(d) / "in.gcode"
        p.write_text(
            "G91\n"
            "G1 X10 Y0\n",
            encoding="utf-8"
        )

        with pytest.raises(SystemExit):
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
    finally:
        shutil.rmtree(d)
