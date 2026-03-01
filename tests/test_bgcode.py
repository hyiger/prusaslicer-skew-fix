"""Tests for binary G-code (.bgcode) read/write/roundtrip support."""

from __future__ import annotations

import math
import struct
import zlib

import pytest


# ---------------------------------------------------------------------------
# Local helper: build a minimal valid .bgcode file from a G-code text string
# and an optional list of pre-built extra blocks (e.g. metadata).
# This mirrors the structure produced by _bgcode_reassemble so we can test
# _bgcode_split on known inputs without depending on module internals.
# ---------------------------------------------------------------------------

def _make_bgcode(gcode_text: str, extra_blocks: list[bytes] = ()) -> bytes:
    """Create a minimal valid .bgcode file containing gcode_text.

    extra_blocks are inserted between the file header and the GCode block,
    matching the PrusaSlicer / libbgcode convention (metadata before GCode).
    """
    MAGIC = b"GCDE"
    VERSION = 1
    CKSUM_CRC32 = 1
    BLK_GCODE = 1
    COMP_NONE = 0
    ENC_RAW = 0

    def _crc(data: bytes, prev: int = 0) -> int:
        return zlib.crc32(data, prev) & 0xFFFFFFFF

    file_hdr = MAGIC + struct.pack("<IH", VERSION, CKSUM_CRC32)
    payload = gcode_text.encode("utf-8")
    hdr = struct.pack("<HHI", BLK_GCODE, COMP_NONE, len(payload))
    params = struct.pack("<H", ENC_RAW)
    cksum = _crc(hdr)
    cksum = _crc(params, cksum)
    cksum = _crc(payload, cksum)
    gcode_block = hdr + params + payload + struct.pack("<I", cksum)

    return file_hdr + b"".join(extra_blocks) + gcode_block


def _make_meta_block(btype: int, content: bytes) -> bytes:
    """Build a minimal metadata block (non-GCode, ENC_INI, COMP_NONE)."""
    COMP_NONE = 0
    ENC_INI = 0

    def _crc(data: bytes, prev: int = 0) -> int:
        return zlib.crc32(data, prev) & 0xFFFFFFFF

    hdr = struct.pack("<HHI", btype, COMP_NONE, len(content))
    params = struct.pack("<H", ENC_INI)
    cksum = _crc(hdr)
    cksum = _crc(params, cksum)
    cksum = _crc(content, cksum)
    return hdr + params + content + struct.pack("<I", cksum)


# ---------------------------------------------------------------------------
# _is_bgcode
# ---------------------------------------------------------------------------

def test_is_bgcode_true(tmp_path, load_module):
    m = load_module
    p = tmp_path / "test.bgcode"
    p.write_bytes(_make_bgcode("G1 X10\n"))
    assert m._is_bgcode(str(p)) is True


def test_is_bgcode_false_text(tmp_path, load_module):
    m = load_module
    p = tmp_path / "test.gcode"
    p.write_text("G1 X10\n", encoding="utf-8")
    assert m._is_bgcode(str(p)) is False


def test_is_bgcode_false_missing_file(tmp_path, load_module):
    m = load_module
    assert m._is_bgcode(str(tmp_path / "nonexistent.bgcode")) is False


# ---------------------------------------------------------------------------
# _bgcode_split
# ---------------------------------------------------------------------------

def test_bgcode_split_basic(load_module):
    m = load_module
    text = "G28\nG1 X10 Y20 F3000\nG1 X20 Y30 E1.0\n"
    data = _make_bgcode(text)
    file_hdr, other_blocks, gcode_text = m._bgcode_split(data)
    assert file_hdr[:4] == b"GCDE"
    assert len(file_hdr) == 10
    assert other_blocks == []
    assert gcode_text == text


def test_bgcode_split_bad_magic(load_module):
    m = load_module
    with pytest.raises(ValueError, match="Not a bgcode"):
        m._bgcode_split(b"NOTGCODE" + b"\x00" * 20)


def test_bgcode_split_too_short(load_module):
    m = load_module
    with pytest.raises(ValueError, match="too short"):
        m._bgcode_split(b"GCDE\x01")


def test_bgcode_split_crc_mismatch(load_module):
    m = load_module
    data = bytearray(_make_bgcode("G1 X1\n"))
    # Corrupt a byte in the GCode payload (after 10-byte file header + 8-byte block header + 2-byte params)
    data[20] ^= 0xFF
    with pytest.raises(ValueError, match="CRC32 mismatch"):
        m._bgcode_split(bytes(data))


def test_bgcode_split_no_gcode_block(load_module):
    """A file with only metadata blocks (no GCode block) should raise."""
    m = load_module
    MAGIC = b"GCDE"
    file_hdr = MAGIC + struct.pack("<IH", 1, 1)
    meta = _make_meta_block(2, b"key=val\n")   # BLK_SLICER_META = 2
    with pytest.raises(ValueError, match="No GCode blocks"):
        m._bgcode_split(file_hdr + meta)


def test_bgcode_split_preserves_other_blocks(load_module):
    """Non-GCode blocks are returned verbatim in other_blocks."""
    m = load_module
    BLK_SLICER_META = 2
    meta = _make_meta_block(BLK_SLICER_META, b"generator=test\n")
    gcode = "G1 X5 Y5\n"
    data = _make_bgcode(gcode, extra_blocks=[meta])
    file_hdr, other_blocks, gcode_text = m._bgcode_split(data)
    assert len(other_blocks) == 1
    assert other_blocks[0] == meta
    assert gcode_text == gcode


def test_bgcode_split_multiple_other_blocks(load_module):
    """Multiple non-GCode blocks all appear in other_blocks in order."""
    m = load_module
    BLK_PRINTER_META = 3
    BLK_SLICER_META = 2
    b1 = _make_meta_block(BLK_PRINTER_META, b"model=CoreOne\n")
    b2 = _make_meta_block(BLK_SLICER_META, b"version=2.9\n")
    data = _make_bgcode("G1 X1\n", extra_blocks=[b1, b2])
    _, other_blocks, _ = m._bgcode_split(data)
    assert len(other_blocks) == 2
    assert other_blocks[0] == b1
    assert other_blocks[1] == b2


# ---------------------------------------------------------------------------
# _bgcode_reassemble
# ---------------------------------------------------------------------------

def test_bgcode_reassemble_roundtrip(load_module):
    """Reassemble a split file and re-split — should recover identical text."""
    m = load_module
    text = "G90\nM82\nG1 X10 Y20 E1.0\n"
    data = _make_bgcode(text)
    file_hdr, other_blocks, _ = m._bgcode_split(data)
    new_text = "G90\nM82\nG1 X11 Y20 E1.0\n"
    rebuilt = m._bgcode_reassemble(file_hdr, other_blocks, new_text)
    _, _, recovered = m._bgcode_split(rebuilt)
    assert recovered == new_text


def test_bgcode_reassemble_starts_with_gcde(load_module):
    m = load_module
    data = _make_bgcode("G1 X1\n")
    file_hdr, other_blocks, _ = m._bgcode_split(data)
    rebuilt = m._bgcode_reassemble(file_hdr, other_blocks, "G1 X2\n")
    assert rebuilt[:4] == b"GCDE"


def test_bgcode_reassemble_preserves_meta_blocks(load_module):
    """After reassemble→split the non-GCode blocks are bit-for-bit identical."""
    m = load_module
    BLK_PRINT_META = 4
    meta = _make_meta_block(BLK_PRINT_META, b"layer_height=0.2\n")
    data = _make_bgcode("G1 X5\n", extra_blocks=[meta])
    file_hdr, other_blocks, _ = m._bgcode_split(data)
    rebuilt = m._bgcode_reassemble(file_hdr, other_blocks, "G1 X6\n")
    _, other_back, _ = m._bgcode_split(rebuilt)
    assert len(other_back) == 1
    assert other_back[0] == meta


# ---------------------------------------------------------------------------
# rewrite() — binary path
# ---------------------------------------------------------------------------

def test_rewrite_bgcode_is_bgcode_output(tmp_path, load_module):
    """After rewrite the output file must still be a valid bgcode."""
    m = load_module
    gcode = "G90\nM82\nG1 X10 Y10 E1.0\n"
    p = tmp_path / "test.bgcode"
    p.write_bytes(_make_bgcode(gcode))

    m.rewrite(
        str(p),
        skew_deg=0.5,
        recenter=False,
        bed_x_min=0, bed_x_max=250,
        bed_y_min=0, bed_y_max=220,
        margin=0,
        recenter_mode="center",
        shear_y_ref_mode="fixed",
        shear_y_ref=0.0,
        analyze_only=False,
    )

    raw = p.read_bytes()
    assert raw[:4] == b"GCDE"
    # Must be parseable
    _, _, result = m._bgcode_split(raw)
    assert "G1" in result


def test_rewrite_bgcode_x_is_modified(tmp_path, load_module):
    """X coordinate must be shifted by the skew transform."""
    m = load_module
    gcode = "G90\nM82\nG1 X10 Y10 E1.0\n"
    p = tmp_path / "test.bgcode"
    p.write_bytes(_make_bgcode(gcode))

    skew_deg = 1.0
    m.rewrite(
        str(p),
        skew_deg=skew_deg,
        recenter=False,
        bed_x_min=0, bed_x_max=250,
        bed_y_min=0, bed_y_max=220,
        margin=0,
        recenter_mode="center",
        shear_y_ref_mode="fixed",
        shear_y_ref=0.0,
        analyze_only=False,
    )

    _, _, result = m._bgcode_split(p.read_bytes())
    # x' = 10 + (10 - 0) * tan(1°) = 10.175 (to 3 d.p.)
    k = math.tan(math.radians(skew_deg))
    expected_x = round(10 + 10 * k, 3)
    expected_str = f"{expected_x:.3f}".rstrip("0").rstrip(".")
    assert f"X{expected_str}" in result


def test_rewrite_bgcode_y_unchanged(tmp_path, load_module):
    """Y coordinate must not be modified (transform is XY-shear, y' = y)."""
    m = load_module
    gcode = "G90\nM82\nG1 X10 Y15 E1.0\n"
    p = tmp_path / "test.bgcode"
    p.write_bytes(_make_bgcode(gcode))

    m.rewrite(
        str(p),
        skew_deg=1.0,
        recenter=False,
        bed_x_min=0, bed_x_max=250,
        bed_y_min=0, bed_y_max=220,
        margin=0,
        recenter_mode="center",
        shear_y_ref_mode="fixed",
        shear_y_ref=0.0,
        analyze_only=False,
    )

    _, _, result = m._bgcode_split(p.read_bytes())
    assert "Y15" in result


def test_rewrite_bgcode_preserves_non_gcode_blocks(tmp_path, load_module):
    """Thumbnail and metadata blocks must survive the rewrite intact."""
    m = load_module
    BLK_PRINTER_META = 3
    BLK_SLICER_META = 2
    b_printer = _make_meta_block(BLK_PRINTER_META, b"model=CoreOne\n")
    b_slicer = _make_meta_block(BLK_SLICER_META, b"version=2.9.4\n")

    gcode = "G90\nM82\nG1 X10 Y10 E1.0\n"
    p = tmp_path / "test.bgcode"
    original_data = _make_bgcode(gcode, extra_blocks=[b_printer, b_slicer])
    p.write_bytes(original_data)

    m.rewrite(
        str(p),
        skew_deg=0.5,
        recenter=False,
        bed_x_min=0, bed_x_max=250,
        bed_y_min=0, bed_y_max=220,
        margin=0,
        recenter_mode="center",
        shear_y_ref_mode="fixed",
        shear_y_ref=0.0,
        analyze_only=False,
    )

    _, other_blocks, _ = m._bgcode_split(p.read_bytes())
    assert len(other_blocks) == 2
    assert other_blocks[0] == b_printer
    assert other_blocks[1] == b_slicer


def test_rewrite_bgcode_zero_skew_gcode_equivalent(tmp_path, load_module):
    """With zero skew the G-code content should be equivalent to text rewrite."""
    m = load_module
    gcode = "G90\nM82\nG1 X10 Y10 E1.0\n"

    p_bg = tmp_path / "test.bgcode"
    p_bg.write_bytes(_make_bgcode(gcode))
    p_txt = tmp_path / "test.gcode"
    p_txt.write_text(gcode, encoding="utf-8")

    kwargs = dict(
        skew_deg=0.0,
        recenter=False,
        bed_x_min=0, bed_x_max=250,
        bed_y_min=0, bed_y_max=220,
        margin=0,
        recenter_mode="center",
        shear_y_ref_mode="fixed",
        shear_y_ref=0.0,
        analyze_only=False,
    )
    m.rewrite(str(p_bg), **kwargs)
    m.rewrite(str(p_txt), **kwargs)

    _, _, bg_result = m._bgcode_split(p_bg.read_bytes())
    txt_result = p_txt.read_text(encoding="utf-8")
    # Both should contain the same skewed (identity) move line
    assert "X10" in bg_result
    assert "X10" in txt_result


def test_rewrite_bgcode_analyze_only_does_not_modify(tmp_path, load_module, capsys):
    """analyze_only must print output but leave the .bgcode file unchanged."""
    m = load_module
    gcode = "G90\nM82\nG1 X10 Y10 E1.0\n"
    p = tmp_path / "test.bgcode"
    original = _make_bgcode(gcode)
    p.write_bytes(original)

    m.rewrite(
        str(p),
        skew_deg=1.0,
        recenter=False,
        bed_x_min=0, bed_x_max=250,
        bed_y_min=0, bed_y_max=220,
        margin=0,
        recenter_mode="center",
        shear_y_ref_mode="fixed",
        shear_y_ref=0.0,
        analyze_only=True,
    )

    assert p.read_bytes() == original
    captured = capsys.readouterr()
    assert "analyze-only" in captured.out


def test_rewrite_bgcode_with_recenter(tmp_path, load_module):
    """Recenter path should work without error for bgcode input."""
    m = load_module
    gcode = "G90\nM82\nG1 X100 Y100 E1.0\nG1 X110 Y110 E2.0\n"
    p = tmp_path / "test.bgcode"
    p.write_bytes(_make_bgcode(gcode))

    m.rewrite(
        str(p),
        skew_deg=0.5,
        recenter=True,
        bed_x_min=0, bed_x_max=250,
        bed_y_min=0, bed_y_max=220,
        margin=5,
        recenter_mode="clamp",
        shear_y_ref_mode="auto",
        shear_y_ref=0.0,
        analyze_only=False,
    )

    raw = p.read_bytes()
    assert raw[:4] == b"GCDE"
    _, _, result = m._bgcode_split(raw)
    assert "prusaslicer-skew-fix" in result


def test_rewrite_bgcode_metadata_comment_present(tmp_path, load_module):
    """The skew-fix metadata comment block must appear in the output gcode."""
    m = load_module
    gcode = "G90\nM82\nG1 X5 Y5 E0.5\n"
    p = tmp_path / "test.bgcode"
    p.write_bytes(_make_bgcode(gcode))

    m.rewrite(
        str(p),
        skew_deg=0.3,
        recenter=False,
        bed_x_min=0, bed_x_max=250,
        bed_y_min=0, bed_y_max=220,
        margin=0,
        recenter_mode="center",
        shear_y_ref_mode="fixed",
        shear_y_ref=0.0,
        analyze_only=False,
    )

    _, _, result = m._bgcode_split(p.read_bytes())
    assert "prusaslicer-skew-fix: applied XY skew correction" in result
    assert "skew_deg=0.3" in result
