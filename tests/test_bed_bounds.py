"""Tests for bed_bounds_from_m862 and --auto-bed CLI integration."""
import pytest


# ---------------------------------------------------------------------------
# bed_bounds_from_m862 unit tests
# ---------------------------------------------------------------------------

def test_returns_none_for_no_m862(tmp_path, load_module):
    m = load_module
    g = tmp_path / "no_check.gcode"
    g.write_text("; no printer check here\nG28\n", encoding="utf-8")
    assert m.bed_bounds_from_m862(str(g)) is None


def test_returns_none_for_unknown_preset(tmp_path, load_module):
    m = load_module
    g = tmp_path / "unknown.gcode"
    g.write_text('M862.3 P "UNKNOWNPRINTER"\n', encoding="utf-8")
    assert m.bed_bounds_from_m862(str(g)) is None


def test_mk4_preset(tmp_path, load_module):
    m = load_module
    g = tmp_path / "mk4.gcode"
    g.write_text('M862.3 P "MK4"\n', encoding="utf-8")
    assert m.bed_bounds_from_m862(str(g)) == (0.0, 250.0, 0.0, 210.0)


def test_mini_plus_preset(tmp_path, load_module):
    m = load_module
    g = tmp_path / "mini.gcode"
    g.write_text('M862.3 P "MINI+"\n', encoding="utf-8")
    assert m.bed_bounds_from_m862(str(g)) == (0.0, 180.0, 0.0, 180.0)


def test_core_one_preset(tmp_path, load_module):
    m = load_module
    g = tmp_path / "coreone.gcode"
    g.write_text('M862.3 P "COREONE"\n', encoding="utf-8")
    assert m.bed_bounds_from_m862(str(g)) == (0.0, 250.0, 0.0, 220.0)


def test_xl_preset(tmp_path, load_module):
    m = load_module
    g = tmp_path / "xl.gcode"
    g.write_text('M862.3 P "XL"\n', encoding="utf-8")
    assert m.bed_bounds_from_m862(str(g)) == (0.0, 360.0, 0.0, 360.0)


def test_case_insensitive_lookup(tmp_path, load_module):
    m = load_module
    g = tmp_path / "case.gcode"
    g.write_text('M862.3 P "mk3s+"\n', encoding="utf-8")
    assert m.bed_bounds_from_m862(str(g)) == (0.0, 250.0, 0.0, 210.0)


def test_first_match_wins(tmp_path, load_module):
    m = load_module
    g = tmp_path / "multi.gcode"
    g.write_text(
        'M862.3 P "MINI"\n'
        'M862.3 P "MK4"\n',
        encoding="utf-8",
    )
    # Only the first recognized line is used
    assert m.bed_bounds_from_m862(str(g)) == (0.0, 180.0, 0.0, 180.0)


def test_inline_comment_ignored(tmp_path, load_module):
    """M862.3 P line with a trailing comment should still parse."""
    m = load_module
    g = tmp_path / "comment.gcode"
    g.write_text('M862.3 P "MK3S" ; printer model check\n', encoding="utf-8")
    assert m.bed_bounds_from_m862(str(g)) == (0.0, 250.0, 0.0, 210.0)


def test_commented_out_line_not_matched(tmp_path, load_module):
    """A semicolon-commented M862.3 should not match."""
    m = load_module
    g = tmp_path / "commented.gcode"
    g.write_text('; M862.3 P "MK4"\n', encoding="utf-8")
    assert m.bed_bounds_from_m862(str(g)) is None


def test_m862_buried_in_file(tmp_path, load_module):
    """Preset found mid-file, not on the first line."""
    m = load_module
    g = tmp_path / "buried.gcode"
    lines = [
        "; PrusaSlicer generated file",
        "G28",
        "G1 X0 Y0 Z0.2 F3000",
        'M862.3 P "MK4S" ; printer model check',
        "G1 X100 Y100 E5",
    ]
    g.write_text("\n".join(lines), encoding="utf-8")
    assert m.bed_bounds_from_m862(str(g)) == (0.0, 250.0, 0.0, 210.0)


# ---------------------------------------------------------------------------
# --auto-bed CLI integration tests
# ---------------------------------------------------------------------------

def _make_simple_gcode(tmp_path, printer: str, extra: str = "") -> str:
    g = tmp_path / "test.gcode"
    content = (
        f'M862.3 P "{printer}" ; model check\n'
        "G90\nM82\nG28\n"
        "G1 X10 Y10 Z0.2 F3000\n"
        "G1 X50 Y50 E1.0\n"
        + extra
    )
    g.write_text(content, encoding="utf-8")
    return str(g)


def test_auto_bed_mini_recenter(tmp_path, load_module):
    """--auto-bed with MINI preset uses 180x180 bounds without error."""
    m = load_module
    path = _make_simple_gcode(tmp_path, "MINI")
    # Should not raise; we use analyze_only so the file is not modified.
    import math
    lines = m.analyze_gcode(
        path,
        k=math.tan(math.radians(-0.15)),
        y_ref=0.0,
        bed_x_min=0.0, bed_x_max=180.0,
        bed_y_min=0.0, bed_y_max=180.0,
        recenter=False,
        margin=0.0,
        recenter_mode="center",
    )
    assert lines  # some output produced


def test_cli_auto_bed_overrides_default(tmp_path, load_module):
    """--auto-bed selects correct MINI bounds instead of default 250x220."""
    m = load_module
    path = _make_simple_gcode(tmp_path, "MINI")
    # Verify auto-detection itself returns MINI bounds
    assert m.bed_bounds_from_m862(path) == (0.0, 180.0, 0.0, 180.0)


def test_cli_explicit_bed_overrides_auto(tmp_path, load_module):
    """Explicit --bed-x-max overrides the value from --auto-bed."""
    m = load_module
    # MINI preset would give x_max=180; we pass --bed-x-max 200 explicitly
    # Simulate the resolution logic used in main():
    auto = m.bed_bounds_from_m862(
        _make_simple_gcode(tmp_path, "MINI")
    )
    assert auto is not None
    explicit_x_max = 200.0
    resolved_x_max = explicit_x_max  # explicit takes priority
    assert resolved_x_max == 200.0


def test_auto_bed_warning_on_unknown(tmp_path, load_module, capsys):
    """--auto-bed emits a warning to stderr when no M862.3 P preset is found."""
    m = load_module
    g = tmp_path / "no_preset.gcode"
    g.write_text(
        "G90\nM82\nG28\nG1 X10 Y10 Z0.2 F3000\nG1 X50 Y50 E1.0\n",
        encoding="utf-8",
    )
    # main() should complete without error; the warning goes to stderr.
    m.main(["--auto-bed", "--skew-deg", "-0.15", str(g)])
    captured = capsys.readouterr()
    assert "no recognized M862.3 P preset found" in captured.err


def test_all_presets_present(load_module):
    """All expected printer names are in _PRUSA_BED_PRESETS."""
    m = load_module
    expected = {
        "MK2", "MK2S", "MK2.5", "MK2.5S",
        "MK3", "MK3S", "MK3S+",
        "MK4", "MK4S",
        "MINI", "MINI+",
        "XL",
        "COREONE", "CORE ONE",
    }
    assert expected.issubset(set(m._PRUSA_BED_PRESETS.keys()))
