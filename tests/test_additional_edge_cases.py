import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = str(Path(__file__).resolve().parents[1] / "skew_fix_ps.py")


def _run(args):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)


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


def test_modal_parser_handles_whitespace_and_comment(load_module):
    m = load_module
    st = m.State(abs_xy=False, abs_e=False, ij_relative=True)
    assert m._handle_modal_state_line(st, "G90   ; COMMENT")
    assert st.abs_xy is True
    assert m._handle_modal_state_line(st, "M82 ; COMMENT")
    assert st.abs_e is True


def test_checksum_style_move_line_is_rewritten_and_checksum_suffix_preserved(tmp_path, load_module):
    m = load_module
    g = tmp_path / "chk.gcode"
    original = "G90\nM82\nG1 X10 Y10 E1*12\n"
    g.write_text(original, encoding="utf-8")
    m.rewrite(
        str(g),
        skew_deg=-0.15,
        recenter=False,
        bed_x_min=0.0, bed_x_max=250.0, bed_y_min=0.0, bed_y_max=220.0, margin=0.0,
        recenter_mode="center",
        shear_y_ref_mode="fixed", shear_y_ref=0.0,
        analyze_only=False,
    )
    out = g.read_text(encoding="utf-8")
    line = next(ln for ln in out.splitlines() if ln.startswith("G1 "))
    assert "*12" in line
    assert "X10 " not in line


def test_relative_e_arc_with_non_positive_e_not_counted_in_bounds(tmp_path, load_module):
    m = load_module
    g = tmp_path / "arc_rel_e.gcode"
    g.write_text(
        "G90\n"
        "M83\n"
        "G0 X10 Y10\n"
        "G2 X20 Y10 I5 J0 E0\n"      # non-extruding in M83
        "G3 X10 Y10 I-5 J0 E-0.5\n"  # non-extruding in M83
        "\n",
        encoding="utf-8",
    )
    dx, dy, bounds = m.compute_translation_for_bounds(
        str(g),
        k=0.0,
        y_ref=0.0,
        bed_x_min=0.0, bed_x_max=250.0,
        bed_y_min=0.0, bed_y_max=220.0,
        margin=0.0,
        recenter_mode="clamp",
    )
    assert dx == 0.0
    assert dy == 0.0
    assert bounds == (0.0, 0.0, 0.0, 0.0)


def test_margin_tiny_positive_tightens_fit_threshold(tmp_path, load_module):
    m = load_module
    g = tmp_path / "fit.gcode"
    g.write_text(
        "G90\n"
        "M82\n"
        "G1 X0 Y0 E1\n"
        "G1 X10 Y10 E2\n"
        "\n",
        encoding="utf-8",
    )

    # Fits exactly at margin=0.
    dx0, dy0, _ = m.compute_translation_for_bounds(
        str(g),
        k=0.0,
        y_ref=0.0,
        bed_x_min=0.0, bed_x_max=10.0,
        bed_y_min=0.0, bed_y_max=10.0,
        margin=0.0,
        recenter_mode="clamp",
    )
    assert dx0 == 0.0 and dy0 == 0.0

    # Tiny positive margin should make exact-edge fit impossible.
    with pytest.raises(SystemExit, match="cannot fit within bed"):
        m.compute_translation_for_bounds(
            str(g),
            k=0.0,
            y_ref=0.0,
            bed_x_min=0.0, bed_x_max=10.0,
            bed_y_min=0.0, bed_y_max=10.0,
            margin=1e-6,
            recenter_mode="clamp",
        )
