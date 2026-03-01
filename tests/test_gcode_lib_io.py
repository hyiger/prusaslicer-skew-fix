"""Tests for gcode_lib I/O: from_text, to_text, load, save, bgcode roundtrip."""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gcode_lib as gl


# ---------------------------------------------------------------------------
# Helpers: build minimal valid .bgcode bytes (no dependency on gcode_lib internals)
# ---------------------------------------------------------------------------

def _make_bgcode(gcode_text: str, extra_blocks: list[bytes] = ()) -> bytes:
    MAGIC       = b"GCDE"
    BLK_GCODE   = 1
    COMP_NONE   = 0
    ENC_RAW     = 0

    file_hdr = MAGIC + struct.pack("<IH", 1, 1)
    payload  = gcode_text.encode("utf-8")
    hdr      = struct.pack("<HHI", BLK_GCODE, COMP_NONE, len(payload))
    params   = struct.pack("<H", ENC_RAW)
    cksum    = zlib.crc32(hdr) & 0xFFFFFFFF
    cksum    = zlib.crc32(params, cksum) & 0xFFFFFFFF
    cksum    = zlib.crc32(payload, cksum) & 0xFFFFFFFF
    gcode_block = hdr + params + payload + struct.pack("<I", cksum)
    return file_hdr + b"".join(extra_blocks) + gcode_block


def _make_meta_block(btype: int, content: bytes) -> bytes:
    COMP_NONE = 0
    ENC_INI   = 0
    hdr    = struct.pack("<HHI", btype, COMP_NONE, len(content))
    params = struct.pack("<H", ENC_INI)
    cksum  = zlib.crc32(hdr) & 0xFFFFFFFF
    cksum  = zlib.crc32(params, cksum) & 0xFFFFFFFF
    cksum  = zlib.crc32(content, cksum) & 0xFFFFFFFF
    return hdr + params + content + struct.pack("<I", cksum)


def _make_thumbnail_block(img_data: bytes, width: int = 16, height: int = 16, fmt: int = 0) -> bytes:
    BLK_THUMBNAIL = 5
    COMP_NONE     = 0
    hdr    = struct.pack("<HHI", BLK_THUMBNAIL, COMP_NONE, len(img_data))
    params = struct.pack("<HHH", width, height, fmt)
    cksum  = zlib.crc32(hdr) & 0xFFFFFFFF
    cksum  = zlib.crc32(params, cksum) & 0xFFFFFFFF
    cksum  = zlib.crc32(img_data, cksum) & 0xFFFFFFFF
    return hdr + params + img_data + struct.pack("<I", cksum)


# ---------------------------------------------------------------------------
# from_text / to_text
# ---------------------------------------------------------------------------

def test_from_text_returns_gcode_file():
    gf = gl.from_text("G90\nG1 X10 Y20\n")
    assert isinstance(gf, gl.GCodeFile)
    assert gf.source_format == "text"
    assert len(gf.lines) == 2


def test_from_text_empty():
    gf = gl.from_text("")
    assert gf.lines == []


def test_to_text_roundtrip():
    text = "G90\nM82\nG1 X10 Y20 E1.0\n"
    gf   = gl.from_text(text)
    out  = gl.to_text(gf)
    # Lines should be the same; to_text adds a trailing newline
    assert out == text


def test_to_text_trailing_newline():
    gf  = gl.from_text("G1 X10")
    out = gl.to_text(gf)
    assert out.endswith("\n")


def test_to_text_preserves_comments():
    text = "G1 X10 ; comment here\n"
    assert gl.to_text(gl.from_text(text)) == text


# ---------------------------------------------------------------------------
# load — text files
# ---------------------------------------------------------------------------

def test_load_text_file(tmp_path):
    p = tmp_path / "test.gcode"
    p.write_text("G90\nG1 X10 Y20\n", encoding="utf-8")
    gf = gl.load(str(p))
    assert gf.source_format == "text"
    assert len(gf.lines) == 2


def test_load_text_file_line_content(tmp_path):
    p = tmp_path / "test.gcode"
    p.write_text("G1 X5 Y10 E0.5\n", encoding="utf-8")
    gf = gl.load(str(p))
    assert gf.lines[0].command == "G1"
    assert gf.lines[0].words["X"] == pytest.approx(5.0)


def test_load_rejects_binary_non_bgcode(tmp_path):
    p = tmp_path / "bad.bin"
    p.write_bytes(b"\x00\x01\x02\x03binary data")
    with pytest.raises(ValueError, match="binary"):
        gl.load(str(p))


# ---------------------------------------------------------------------------
# load — bgcode files
# ---------------------------------------------------------------------------

def test_load_bgcode_file(tmp_path):
    p = tmp_path / "test.bgcode"
    p.write_bytes(_make_bgcode("G90\nG1 X10\n"))
    gf = gl.load(str(p))
    assert gf.source_format == "bgcode"
    assert any(ln.command == "G1" for ln in gf.lines)


def test_load_bgcode_thumbnails_extracted(tmp_path):
    img = b"\xff\xd8\xff\xe0" + b"\x00" * 60   # fake JPEG-ish bytes
    thumb_block = _make_thumbnail_block(img, width=16, height=16, fmt=1)
    p = tmp_path / "test.bgcode"
    p.write_bytes(_make_bgcode("G1 X1\n", extra_blocks=[thumb_block]))
    gf = gl.load(str(p))
    assert len(gf.thumbnails) == 1
    assert gf.thumbnails[0].width == 16
    assert gf.thumbnails[0].height == 16
    assert gf.thumbnails[0].data == img


def test_load_bgcode_no_thumbnails(tmp_path):
    p = tmp_path / "test.bgcode"
    p.write_bytes(_make_bgcode("G1 X1\n"))
    gf = gl.load(str(p))
    assert gf.thumbnails == []


# ---------------------------------------------------------------------------
# save — text files
# ---------------------------------------------------------------------------

def test_save_text_file_roundtrip(tmp_path):
    text = "G90\nM82\nG1 X10 Y20 E1.0\n"
    gf   = gl.from_text(text)
    p    = tmp_path / "out.gcode"
    gl.save(gf, str(p))
    assert p.read_text(encoding="utf-8") == text


def test_save_text_creates_file(tmp_path):
    gf = gl.from_text("G1 X5\n")
    p  = tmp_path / "new.gcode"
    gl.save(gf, str(p))
    assert p.exists()


def test_save_text_is_atomic(tmp_path):
    """save() should not leave a .tmp file behind."""
    gf = gl.from_text("G1 X5\n")
    p  = tmp_path / "out.gcode"
    gl.save(gf, str(p))
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


# ---------------------------------------------------------------------------
# save — bgcode files
# ---------------------------------------------------------------------------

def test_save_bgcode_produces_valid_bgcode(tmp_path):
    raw = _make_bgcode("G90\nG1 X10 Y20\n")
    gf  = gl._load_bgcode(raw)
    p   = tmp_path / "out.bgcode"
    gl.save(gf, str(p))
    out = p.read_bytes()
    assert out[:4] == b"GCDE"


def test_save_bgcode_roundtrip_gcode_text(tmp_path):
    text = "G90\nM82\nG1 X10 Y20 E1.0\n"
    raw  = _make_bgcode(text)
    gf   = gl._load_bgcode(raw)
    p    = tmp_path / "out.bgcode"
    gl.save(gf, str(p))
    # Re-load and verify G-code content
    gf2 = gl.load(str(p))
    assert gf2.source_format == "bgcode"
    assert any(ln.command == "G1" for ln in gf2.lines)


def test_save_bgcode_preserves_meta_blocks(tmp_path):
    BLK_SLICER_META = 2
    meta = _make_meta_block(BLK_SLICER_META, b"key=value\n")
    raw  = _make_bgcode("G1 X5\n", extra_blocks=[meta])
    gf   = gl._load_bgcode(raw)
    p    = tmp_path / "out.bgcode"
    gl.save(gf, str(p))
    # Re-split and verify non-gcode blocks intact
    _, nongcode, _, _ = gl._bgcode_split(p.read_bytes())
    assert len(nongcode) == 1
    assert nongcode[0] == meta


def test_save_bgcode_preserves_thumbnail(tmp_path):
    img   = b"\x89PNG\r\n" + b"\x00" * 50
    tblk  = _make_thumbnail_block(img, width=32, height=32, fmt=0)
    raw   = _make_bgcode("G1 X1\n", extra_blocks=[tblk])
    gf    = gl._load_bgcode(raw)
    p     = tmp_path / "out.bgcode"
    gl.save(gf, str(p))
    _, nongcode, thumbs, _ = gl._bgcode_split(p.read_bytes())
    assert len(thumbs) == 1
    assert thumbs[0].data == img
    assert thumbs[0].width == 32


# ---------------------------------------------------------------------------
# _bgcode_split — error handling
# ---------------------------------------------------------------------------

def test_bgcode_split_bad_magic():
    with pytest.raises(ValueError, match="Not a .bgcode"):
        gl._bgcode_split(b"NOTGCDE" + b"\x00" * 20)


def test_bgcode_split_too_short():
    with pytest.raises(ValueError, match="too short"):
        gl._bgcode_split(b"GCDE\x01")


def test_bgcode_split_crc_mismatch():
    data = bytearray(_make_bgcode("G1 X1\n"))
    data[20] ^= 0xFF
    with pytest.raises(ValueError, match="CRC32 mismatch"):
        gl._bgcode_split(bytes(data))


def test_bgcode_split_no_gcode_block():
    MAGIC    = b"GCDE"
    file_hdr = MAGIC + struct.pack("<IH", 1, 1)
    meta     = _make_meta_block(2, b"key=val\n")
    with pytest.raises(ValueError, match="No GCode blocks"):
        gl._bgcode_split(file_hdr + meta)


# ---------------------------------------------------------------------------
# _is_bgcode_file
# ---------------------------------------------------------------------------

def test_is_bgcode_file_true(tmp_path):
    p = tmp_path / "t.bgcode"
    p.write_bytes(_make_bgcode("G1 X1\n"))
    assert gl._is_bgcode_file(str(p)) is True


def test_is_bgcode_file_false(tmp_path):
    p = tmp_path / "t.gcode"
    p.write_text("G1 X1\n", encoding="utf-8")
    assert gl._is_bgcode_file(str(p)) is False


def test_is_bgcode_file_missing(tmp_path):
    assert gl._is_bgcode_file(str(tmp_path / "nonexistent.bgcode")) is False
