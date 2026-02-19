import subprocess
import sys
from pathlib import Path


SCRIPT = str(Path(__file__).resolve().parents[1] / "skew_fix_ps.py")


def _run(args):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)


def test_cli_rejects_multiple_skew_sources_deg_and_square(tmp_path):
    g = tmp_path / "t.gcode"
    g.write_text("G90\nG1 X0 Y0\n", encoding="utf-8")
    r = _run(["--skew-deg", "0.1", "--skew-from-square", "1,1,1", str(g)])
    assert r.returncode != 0
    assert "Specify exactly one" in (r.stderr + r.stdout)


def test_cli_rejects_multiple_skew_sources_square_and_rectangle(tmp_path):
    g = tmp_path / "t.gcode"
    g.write_text("G90\nG1 X0 Y0\n", encoding="utf-8")
    r = _run(["--skew-from-square", "1,1,1", "--skew-from-rectangle", "1,1,1,1", str(g)])
    assert r.returncode != 0
    assert "Specify exactly one" in (r.stderr + r.stdout)


def test_cli_rejects_bad_skew_from_square_shapes(tmp_path):
    g = tmp_path / "t.gcode"
    g.write_text("G90\nG1 X0 Y0\n", encoding="utf-8")

    r_count = _run(["--skew-from-square", "1,2", str(g)])
    assert r_count.returncode != 0
    assert "expects 3 comma-separated numbers" in (r_count.stderr + r_count.stdout)

    r_non_numeric = _run(["--skew-from-square", "1,abc,3", str(g)])
    assert r_non_numeric.returncode != 0
    assert "contains a non-numeric value" in (r_non_numeric.stderr + r_non_numeric.stdout)

    r_non_finite = _run(["--skew-from-square", "nan,1,3", str(g)])
    assert r_non_finite.returncode != 0
    assert "contains a non-finite value" in (r_non_finite.stderr + r_non_finite.stdout)

    r_zero_ad = _run(["--skew-from-square", "1,2,0", str(g)])
    assert r_zero_ad.returncode != 0
    assert "AD must be non-zero" in (r_zero_ad.stderr + r_zero_ad.stdout)


def test_cli_rejects_bad_skew_from_rectangle_shapes(tmp_path):
    g = tmp_path / "t.gcode"
    g.write_text("G90\nG1 X0 Y0\n", encoding="utf-8")

    r_count = _run(["--skew-from-rectangle", "1,2,3", str(g)])
    assert r_count.returncode != 0
    assert "expects 4 comma-separated numbers" in (r_count.stderr + r_count.stdout)

    r_non_numeric = _run(["--skew-from-rectangle", "1,2,3,abc", str(g)])
    assert r_non_numeric.returncode != 0
    assert "contains a non-numeric value" in (r_non_numeric.stderr + r_non_numeric.stdout)

    r_non_finite = _run(["--skew-from-rectangle", "1,2,3,inf", str(g)])
    assert r_non_finite.returncode != 0
    assert "contains a non-finite value" in (r_non_finite.stderr + r_non_finite.stdout)

    r_zero_ad = _run(["--skew-from-rectangle", "1,2,0,4", str(g)])
    assert r_zero_ad.returncode != 0
    assert "AD must be non-zero" in (r_zero_ad.stderr + r_zero_ad.stdout)


def test_skew_from_rectangle_ab_is_currently_ignored(load_module):
    m = load_module
    a = m.skew_deg_from_rectangle("180.5,179.5,80,120")
    b = m.skew_deg_from_rectangle("180.5,179.5,80,999")
    assert a == b


def test_cli_negative_decimal_flags_fail(tmp_path):
    g = tmp_path / "t.gcode"
    g.write_text("G90\nG1 X10 Y10 E1\n", encoding="utf-8")

    r_xy = _run(["--skew-deg", "0.1", "--xy-decimals", "-1", str(g)])
    assert r_xy.returncode != 0
    assert "invalid non-negative int value" in (r_xy.stderr + r_xy.stdout)

    g.write_text("G90\nG1 X10 Y10 E1\n", encoding="utf-8")
    r_other = _run(["--skew-deg", "0.1", "--other-decimals", "-2", str(g)])
    assert r_other.returncode != 0
    assert "invalid non-negative int value" in (r_other.stderr + r_other.stdout)


def test_cli_rejects_non_finite_skew_deg(tmp_path):
    g = tmp_path / "t.gcode"
    g.write_text("G90\nG1 X0 Y0\n", encoding="utf-8")
    r_nan = _run(["--skew-deg", "nan", str(g)])
    assert r_nan.returncode != 0
    assert "invalid finite float value" in (r_nan.stderr + r_nan.stdout)
    r_inf = _run(["--skew-deg", "inf", str(g)])
    assert r_inf.returncode != 0
    assert "invalid finite float value" in (r_inf.stderr + r_inf.stdout)


def test_cli_analyze_only_recenter_rejects_relative_xy(tmp_path):
    g = tmp_path / "rel.gcode"
    g.write_text("G91\nG1 X10 Y0\n", encoding="utf-8")
    r = _run(["--skew-deg", "0.1", "--analyze-only", "--recenter-to-bed", str(g)])
    assert r.returncode != 0
    assert "--recenter-to-bed requires absolute XY" in (r.stderr + r.stdout)


def test_recenter_with_origin_point_is_not_treated_as_empty(tmp_path, load_module):
    m = load_module
    g = tmp_path / "origin.gcode"
    g.write_text("G90\nM82\nG1 X0 Y0 E1\n", encoding="utf-8")
    dx, dy, bounds = m.compute_translation_for_bounds(
        str(g),
        k=0.0,
        y_ref=0.0,
        bed_x_min=0.0,
        bed_x_max=10.0,
        bed_y_min=0.0,
        bed_y_max=10.0,
        margin=0.0,
        recenter_mode="center",
    )
    assert dx == 5.0
    assert dy == 5.0
    assert bounds == (0.0, 0.0, 0.0, 0.0)


def test_cli_rejects_invalid_bed_ranges(tmp_path):
    g = tmp_path / "t.gcode"
    g.write_text("G90\nM82\nG1 X10 Y10 E1\n", encoding="utf-8")
    rx = _run(["--skew-deg", "0", "--recenter-to-bed", "--bed-x-min", "250", "--bed-x-max", "0", str(g)])
    assert rx.returncode != 0
    assert "--bed-x-min must be <=" in (rx.stderr + rx.stdout)
    ry = _run(["--skew-deg", "0", "--recenter-to-bed", "--bed-y-min", "220", "--bed-y-max", "0", str(g)])
    assert ry.returncode != 0
    assert "--bed-y-min must be <=" in (ry.stderr + ry.stdout)


def test_cli_rejects_negative_margin(tmp_path):
    g = tmp_path / "t.gcode"
    g.write_text("G90\nM82\nG1 X10 Y10 E1\n", encoding="utf-8")
    r = _run(["--skew-deg", "0", "--margin", "-0.1", str(g)])
    assert r.returncode != 0
    assert "invalid non-negative float value" in (r.stderr + r.stdout)


def test_cli_rejects_non_finite_shear_y_ref(tmp_path):
    g = tmp_path / "t.gcode"
    g.write_text("G90\nG1 X0 Y0\n", encoding="utf-8")

    r_nan = _run(["--skew-deg", "0.1", "--shear-y-ref-mode", "fixed", "--shear-y-ref", "nan", str(g)])
    assert r_nan.returncode != 0
    assert "invalid finite float value" in (r_nan.stderr + r_nan.stdout)

    r_inf = _run(["--skew-deg", "0.1", "--shear-y-ref-mode", "fixed", "--shear-y-ref", "inf", str(g)])
    assert r_inf.returncode != 0
    assert "invalid finite float value" in (r_inf.stderr + r_inf.stdout)


def test_cli_rejects_non_finite_bed_args(tmp_path):
    g = tmp_path / "t.gcode"
    g.write_text("G90\nG1 X0 Y0\n", encoding="utf-8")

    r_x = _run(["--skew-deg", "0", "--bed-x-min", "nan", str(g)])
    assert r_x.returncode != 0
    assert "invalid finite float value" in (r_x.stderr + r_x.stdout)

    r_y = _run(["--skew-deg", "0", "--bed-y-max", "inf", str(g)])
    assert r_y.returncode != 0
    assert "invalid finite float value" in (r_y.stderr + r_y.stdout)
