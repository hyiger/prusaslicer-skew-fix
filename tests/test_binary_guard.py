import pytest


def test_binary_guard_gcde_at_start_is_rejected(tmp_path, load_module):
    m = load_module
    p = tmp_path / "binary_like.gcode"
    p.write_bytes(b"GCDE" + b"\x01\x02\x03")
    with pytest.raises(SystemExit, match="Binary G-code detected"):
        m.rewrite(
            str(p),
            skew_deg=-0.15,
            recenter=False,
            bed_x_min=0.0, bed_x_max=250.0, bed_y_min=0.0, bed_y_max=220.0, margin=0.0,
            recenter_mode="center",
            shear_y_ref_mode="fixed", shear_y_ref=0.0,
            analyze_only=False
        )


def test_binary_guard_gcde_in_comment_text_is_allowed(tmp_path, load_module):
    m = load_module
    p = tmp_path / "text_with_gcde.gcode"
    p.write_text("; GCDE marker in comment\nG90\nG1 X0 Y0\n", encoding="utf-8")
    # Should process as text G-code.
    m.rewrite(
        str(p),
        skew_deg=0.0,
        recenter=False,
        bed_x_min=0.0, bed_x_max=250.0, bed_y_min=0.0, bed_y_max=220.0, margin=0.0,
        recenter_mode="center",
        shear_y_ref_mode="fixed", shear_y_ref=0.0,
        analyze_only=False
    )
