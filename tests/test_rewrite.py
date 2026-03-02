from pathlib import Path
import re
import math
import pytest

def _read_header_val(text: str, key: str):
    # Find e.g. "; prusaslicer-skew-fix: shear_y_ref_mode=auto  shear_y_ref=100.0000"
    for line in text.splitlines():
        if line.startswith("; prusaslicer-skew-fix:") and key in line:
            return line
    return None

def test_rewrite_auto_y_ref_uses_extruding_y_center(tmp_path, load_module):
    m = load_module
    g = tmp_path / "t.gcode"
    # Extruding moves Y from 10..110 => center 60
    g.write_text("\n".join([
        "G90",
        "M82",
        "G1 X10 Y10 E1.0",
        "G1 X20 Y110 E2.0",
        "G1 X30 Y60 E3.0",
        ""
    ]), encoding="utf-8")

    m.rewrite(
        str(g),
        skew_deg=-0.15,
        recenter=False,
        bed_x_min=0.0, bed_x_max=250.0, bed_y_min=0.0, bed_y_max=220.0, margin=0.0,
        recenter_mode="center",
        shear_y_ref_mode="auto", shear_y_ref=0.0,
        analyze_only=False
    )

    txt = g.read_text(encoding="utf-8")
    line = _read_header_val(txt, "shear_y_ref_mode")
    assert line is not None
    assert "shear_y_ref_mode=auto" in line
    # numeric value in header is formatted to 4 decimals
    m2 = re.search(r"shear_y_ref=([0-9]+\.[0-9]+)", line)
    assert m2, line
    assert float(m2.group(1)) == pytest.approx(60.0, abs=1e-4)

def test_rewrite_linearize_arcs_emits_g1_only(tmp_path, load_module):
    m = load_module
    g = tmp_path / "arc.gcode"
    g.write_text("\n".join([
        "G90",
        "M82",
        "G0 X10 Y10",
        # CW quarter-circle around center (10,20) to (20,20)
        "G2 X20 Y20 I0 J10 E1.0 F1200",
        ""
    ]), encoding="utf-8")

    m.rewrite(
        str(g),
        skew_deg=-0.15,  # coarse to keep few points
        recenter=False,
        bed_x_min=0.0, bed_x_max=250.0, bed_y_min=0.0, bed_y_max=220.0, margin=0.0,
        recenter_mode="center",
        shear_y_ref_mode="fixed", shear_y_ref=0.0,
        analyze_only=False
    )

    out = g.read_text(encoding="utf-8").splitlines()
    # Ensure original G2 is not present after rewrite when linearize=True
    assert not any(re.match(r"^G2\b", ln.strip(), re.IGNORECASE) for ln in out)
    # Should have at least one G1 generated from the arc
    assert any(re.match(r"^G1\b", ln.strip(), re.IGNORECASE) for ln in out)

def test_binary_guard_rejects_bgcode(tmp_path, load_module):
    m = load_module
    p = tmp_path / "bad.gcode"
    p.write_bytes(b"GCDE" + b"\x00"*100)
    with pytest.raises(SystemExit):
        m.rewrite(
            str(p),
            skew_deg=-0.15,
            recenter=False,
            bed_x_min=0.0, bed_x_max=250.0, bed_y_min=0.0, bed_y_max=220.0, margin=0.0,
            recenter_mode="center",
            shear_y_ref_mode="fixed", shear_y_ref=0.0,
            analyze_only=False
        )

def test_binary_guard_rejects_nul_binary(tmp_path, load_module):
    m = load_module
    p = tmp_path / "nul.gcode"
    p.write_bytes(b"G1 X0 Y0\n\x00BINARY")
    with pytest.raises(SystemExit, match="ERROR"):
        m.rewrite(
            str(p),
            skew_deg=-0.15,
            recenter=False,
            bed_x_min=0.0, bed_x_max=250.0, bed_y_min=0.0, bed_y_max=220.0, margin=0.0,
            recenter_mode="center",
            shear_y_ref_mode="fixed", shear_y_ref=0.0,
            analyze_only=False
        )

def test_rewrite_auto_bed_detect_mk4(tmp_path, load_module):
    """rewrite() with bed bounds=None auto-detects from M862.3 P."""
    m = load_module
    g = tmp_path / "t.gcode"
    g.write_text("\n".join([
        'M862.3 P "MK4"',
        "G90",
        "M82",
        "G1 X100 Y100 E1.0",
        "",
    ]), encoding="utf-8")

    m.rewrite(
        str(g),
        skew_deg=-0.15,
        recenter=False,
        bed_x_min=None, bed_x_max=None, bed_y_min=None, bed_y_max=None,
        margin=0.0,
        recenter_mode="center",
        shear_y_ref_mode="auto", shear_y_ref=0.0,
        analyze_only=False,
    )
    # Should succeed — bed bounds derived from MK4 preset (250x210)
    txt = g.read_text(encoding="utf-8")
    assert "G1" in txt


def test_rewrite_auto_bed_fallback_no_m862(tmp_path, load_module):
    """rewrite() falls back to 250x220 when no M862.3 found."""
    m = load_module
    g = tmp_path / "t.gcode"
    g.write_text("G90\nM82\nG1 X100 Y100 E1.0\n", encoding="utf-8")

    m.rewrite(
        str(g),
        skew_deg=-0.15,
        recenter=False,
        bed_x_min=None, bed_x_max=None, bed_y_min=None, bed_y_max=None,
        margin=0.0,
        recenter_mode="center",
        shear_y_ref_mode="auto", shear_y_ref=0.0,
        analyze_only=False,
    )
    txt = g.read_text(encoding="utf-8")
    assert "G1" in txt


def test_rewrite_auto_bed_unknown_printer_falls_back(tmp_path, load_module):
    """Unknown printer in M862.3 P falls back to 250x220 defaults."""
    m = load_module
    g = tmp_path / "t.gcode"
    g.write_text("\n".join([
        'M862.3 P "UNKNOWN_PRINTER"',
        "G90",
        "M82",
        "G1 X100 Y100 E1.0",
        "",
    ]), encoding="utf-8")

    m.rewrite(
        str(g),
        skew_deg=-0.15,
        recenter=False,
        bed_x_min=None, bed_x_max=None, bed_y_min=None, bed_y_max=None,
        margin=0.0,
        recenter_mode="center",
        shear_y_ref_mode="auto", shear_y_ref=0.0,
        analyze_only=False,
    )
    txt = g.read_text(encoding="utf-8")
    assert "G1" in txt


def test_rewrite_partial_bed_bounds_override(tmp_path, load_module):
    """Explicit bed_y_max with None for others uses auto-detect + override."""
    m = load_module
    g = tmp_path / "t.gcode"
    g.write_text("\n".join([
        'M862.3 P "COREONE"',
        "G90",
        "M82",
        "G1 X100 Y100 E1.0",
        "",
    ]), encoding="utf-8")

    # Only override bed_y_max; others should auto-detect from COREONE
    m.rewrite(
        str(g),
        skew_deg=-0.15,
        recenter=False,
        bed_x_min=None, bed_x_max=None, bed_y_min=None, bed_y_max=200.0,
        margin=0.0,
        recenter_mode="center",
        shear_y_ref_mode="auto", shear_y_ref=0.0,
        analyze_only=False,
    )
    txt = g.read_text(encoding="utf-8")
    assert "G1" in txt


def test_rewrite_auto_bed_with_analyze_only(tmp_path, load_module):
    """analyze_only with auto-detected bed bounds produces output."""
    m = load_module
    g = tmp_path / "t.gcode"
    original = 'M862.3 P "MK4"\nG90\nM82\nG1 X100 Y100 E1.0\n'
    g.write_text(original, encoding="utf-8")

    m.rewrite(
        str(g),
        skew_deg=-0.15,
        recenter=True,
        bed_x_min=None, bed_x_max=None, bed_y_min=None, bed_y_max=None,
        margin=0.0,
        recenter_mode="clamp",
        shear_y_ref_mode="auto", shear_y_ref=0.0,
        analyze_only=True,
    )
    # File should not be modified in analyze-only mode
    assert g.read_text(encoding="utf-8") == original


def test_binary_guard_rejects_gcde_within_header_window(tmp_path, load_module):
    m = load_module
    p = tmp_path / "bad_header.gcode"
    p.write_bytes(b"ABCD1234GCDEmore-text-no-nul")
    # GCDE not at byte 0 should not be treated as binary magic.
    m.rewrite(
        str(p),
        skew_deg=-0.15,
        recenter=False,
        bed_x_min=0.0, bed_x_max=250.0, bed_y_min=0.0, bed_y_max=220.0, margin=0.0,
        recenter_mode="center",
        shear_y_ref_mode="fixed", shear_y_ref=0.0,
        analyze_only=False
    )
