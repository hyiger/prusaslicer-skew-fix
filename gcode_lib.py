#!/usr/bin/env python3
"""
gcode_lib — general-purpose G-code manipulation library.

Provides data structures and utilities for parsing, analysing, and
transforming G-code files in both plain-text (.gcode) and Prusa binary
(.bgcode) formats.

Key capabilities
================
- Auto-detect and load text or binary G-code from disk.
- Parse G-code into structured ``GCodeLine`` objects with command, axis
  words, and comment cleanly separated.
- Track G-code modal state (G90/G91, M82/M83, G90.1/G91.1, G92) and
  absolute tool position through a sequence of lines.
- Linearize G2/G3 arcs into G1 segments (configurable precision).
- Apply arbitrary XY transforms: skew correction, translation, or any
  user-supplied ``fn(x, y) -> (x', y')``.
- Compute statistics: move counts, XY/Z bounds, feedrates, layer heights,
  total extrusion.
- Save modified G-code back to disk, preserving file format and all
  non-GCode blocks (thumbnails, metadata) in binary .bgcode files.

Design constraints
==================
- Standard library only (``struct``, ``zlib``, ``re``, ``math``, …).
  No third-party runtime dependencies.
- Python 3.10+; uses ``from __future__ import annotations``.
- Relative XY (G91) is **not** supported for XY transforms.  Callers
  must linearize arcs and confirm G90 is active before transforming.
"""

from __future__ import annotations

import math
import os
import re
import struct
import tempfile
import zlib
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EPS = 1e-9                    # Floating-point comparison tolerance
DEFAULT_ARC_SEG_MM = 0.20     # Max chord length (mm) per linearised arc segment
DEFAULT_ARC_MAX_DEG = 5.0     # Max sweep angle (°) per linearised arc segment
DEFAULT_XY_DECIMALS = 3       # Output decimal places for X/Y axes
DEFAULT_OTHER_DECIMALS = 5    # Output decimal places for E/F/Z/I/J/K

# Pre-compiled regexes
_MOVE_RE = re.compile(r"^(G0|G1)\b", re.IGNORECASE)
_ARC_RE  = re.compile(r"^(G2|G3)\b", re.IGNORECASE)
_NUM_RE  = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_AXIS_RE = re.compile(rf"([XYZEFRIJK])\s*({_NUM_RE})", re.IGNORECASE)

# Binary .bgcode constants
_BGCODE_MAGIC = b"GCDE"
_BLK_GCODE      = 1
_BLK_THUMBNAIL  = 5
_COMP_NONE      = 0
_COMP_DEFLATE   = 1
_ENC_RAW        = 0

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ModalState:
    """G-code modal state, updated line-by-line during parsing.

    Tracks coordinate mode, extrusion mode, arc-centre mode, and the
    current absolute tool position.
    """
    abs_xy: bool = True          # True = G90 (absolute), False = G91 (relative)
    abs_e: bool = True           # True = M82 (absolute E), False = M83 (relative E)
    ij_relative: bool = True     # True = G91.1 (relative IJ), False = G90.1 (absolute IJ)
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    e: float = 0.0
    f: Optional[float] = None

    def copy(self) -> "ModalState":
        """Return an independent copy of this state."""
        return ModalState(
            abs_xy=self.abs_xy,
            abs_e=self.abs_e,
            ij_relative=self.ij_relative,
            x=self.x, y=self.y, z=self.z,
            e=self.e, f=self.f,
        )


@dataclass
class GCodeLine:
    """A single parsed G-code line.

    Attributes
    ----------
    raw:     Original line text (trailing newline stripped).
    command: Uppercased command token (e.g. ``"G1"``, ``"M82"``), or ``""``
             for blank / comment-only lines.
    words:   Axis-word dict parsed from the code portion, e.g.
             ``{"X": 10.0, "Y": 20.0, "E": 1.5}``.
    comment: Comment portion including the leading ``";"``, or ``""``.
    """
    raw: str
    command: str
    words: Dict[str, float]
    comment: str

    @property
    def is_move(self) -> bool:
        """True if this is a G0 or G1 command."""
        return bool(_MOVE_RE.match(self.command))

    @property
    def is_arc(self) -> bool:
        """True if this is a G2 or G3 command."""
        return bool(_ARC_RE.match(self.command))

    @property
    def is_blank(self) -> bool:
        """True if the line carries no command and no meaningful comment."""
        return not self.command and not self.comment.strip("; \t")


@dataclass
class Thumbnail:
    """An image thumbnail extracted from a .bgcode file.

    ``params`` holds the raw 6-byte parameter block (width uint16, height
    uint16, format uint16 per the libbgcode spec).  ``_raw_block`` is the
    verbatim bytes of the full bgcode block, enabling lossless round-trips.
    """
    params: bytes        # 6-byte block params (width, height, fmt_code)
    data: bytes          # Decompressed image bytes
    _raw_block: bytes    # Full block bytes for verbatim reassembly

    @property
    def width(self) -> int:
        """Image width in pixels."""
        return struct.unpack_from("<H", self.params, 0)[0]

    @property
    def height(self) -> int:
        """Image height in pixels."""
        return struct.unpack_from("<H", self.params, 2)[0]

    @property
    def format_code(self) -> int:
        """Raw format code from the bgcode thumbnail block params."""
        return struct.unpack_from("<H", self.params, 4)[0]


@dataclass
class GCodeFile:
    """Parsed G-code file — top-level container for this library.

    Attributes
    ----------
    lines:         All G-code lines in source order.
    thumbnails:    Thumbnail images (populated only for .bgcode sources).
    source_format: ``"text"`` or ``"bgcode"``.
    """
    lines: List[GCodeLine]
    thumbnails: List[Thumbnail]
    source_format: str                               # "text" | "bgcode"
    _bgcode_file_hdr: Optional[bytes] = field(default=None, repr=False)
    _bgcode_nongcode_blocks: Optional[List[bytes]] = field(default=None, repr=False)


@dataclass
class Bounds:
    """Axis-aligned bounding box computed from G-code move endpoints.

    Starts at ±∞; call ``expand(x, y)`` to include points.  Check
    ``valid`` before reading coordinates when the input may be empty.
    """
    x_min: float = float("inf")
    x_max: float = float("-inf")
    y_min: float = float("inf")
    y_max: float = float("-inf")
    z_min: float = float("inf")
    z_max: float = float("-inf")

    @property
    def valid(self) -> bool:
        """True if at least one XY point has been added."""
        return self.x_min != float("inf")

    def expand(self, x: float, y: float) -> None:
        """Expand the XY box to include ``(x, y)``."""
        if x < self.x_min: self.x_min = x
        if x > self.x_max: self.x_max = x
        if y < self.y_min: self.y_min = y
        if y > self.y_max: self.y_max = y

    def expand_z(self, z: float) -> None:
        """Expand the Z range to include ``z``."""
        if z < self.z_min: self.z_min = z
        if z > self.z_max: self.z_max = z

    @property
    def width(self) -> float:
        """X extent ``(x_max - x_min)``, or 0.0 if not valid."""
        return self.x_max - self.x_min if self.valid else 0.0

    @property
    def height(self) -> float:
        """Y extent ``(y_max - y_min)``, or 0.0 if not valid."""
        return self.y_max - self.y_min if self.valid else 0.0

    @property
    def center_x(self) -> float:
        """X midpoint, or 0.0 if not valid."""
        return 0.5 * (self.x_min + self.x_max) if self.valid else 0.0

    @property
    def center_y(self) -> float:
        """Y midpoint, or 0.0 if not valid."""
        return 0.5 * (self.y_min + self.y_max) if self.valid else 0.0


@dataclass
class GCodeStats:
    """Statistical summary of a G-code line sequence."""
    total_lines: int = 0
    blank_lines: int = 0
    comment_only_lines: int = 0
    move_count: int = 0          # G0 + G1 total
    arc_count: int = 0           # G2 + G3 total
    travel_count: int = 0        # non-extruding moves
    extrude_count: int = 0       # moves with positive extrusion delta
    retract_count: int = 0       # moves with negative extrusion delta
    total_extrusion: float = 0.0
    bounds: Bounds = field(default_factory=Bounds)
    z_heights: List[float] = field(default_factory=list)   # unique Z values, in order
    feedrates: List[float] = field(default_factory=list)   # unique F values, in order

    @property
    def layer_count(self) -> int:
        """Approximate layer count (number of unique Z heights seen)."""
        return len(self.z_heights)


# ---------------------------------------------------------------------------
# Parsing utilities
# ---------------------------------------------------------------------------

def split_comment(line: str) -> Tuple[str, str]:
    """Split a G-code line into ``(code, comment)`` at the first ``';'``.

    The comment string includes the leading ``";"`` if present.
    Trailing whitespace is stripped from the code portion.

    >>> split_comment("G1 X10 Y20 ; move to start")
    ('G1 X10 Y20', '; move to start')
    >>> split_comment("G1 X10 Y20")
    ('G1 X10 Y20', '')
    """
    if ";" in line:
        code, comment = line.split(";", 1)
        return code.rstrip(), ";" + comment
    return line.rstrip(), ""


def parse_words(code: str) -> Dict[str, float]:
    """Parse axis words from a G-code command string.

    Returns a ``{axis: value}`` dict for every recognised axis letter
    (X/Y/Z/E/F/I/J/K/R) found in *code*.  Letter keys are always uppercase.

    >>> parse_words("G1 X10.5 Y-3 E0.1")
    {'X': 10.5, 'Y': -3.0, 'E': 0.1}
    """
    return {m.group(1).upper(): float(m.group(2)) for m in _AXIS_RE.finditer(code)}


def parse_line(raw_line: str) -> GCodeLine:
    """Parse one line of G-code text into a :class:`GCodeLine`.

    The trailing newline is stripped before processing.
    """
    line = raw_line.rstrip("\n")
    code, comment = split_comment(line)
    s = code.strip()
    parts = s.split(None, 1)
    command = parts[0].upper() if parts else ""
    words = parse_words(code) if s else {}
    return GCodeLine(raw=line, command=command, words=words, comment=comment)


def parse_lines(text: str) -> List[GCodeLine]:
    """Parse a multi-line G-code string into a list of :class:`GCodeLine` objects."""
    return [parse_line(ln) for ln in text.splitlines()]


# ---------------------------------------------------------------------------
# Modal state management
# ---------------------------------------------------------------------------

def advance_state(state: ModalState, line: GCodeLine) -> None:
    """Update *state* in-place to reflect the given *line*.

    Handles:
    - Mode changes: G90/G91, M82/M83, G90.1/G91.1
    - G0/G1 linear moves (absolute and relative XY/Z)
    - G2/G3 arc moves (endpoint update only)
    - G92 position reset
    - E and F tracking on all motion commands
    """
    cmd = line.command
    words = line.words

    # --- Modal flag updates ---
    if cmd == "G90":
        state.abs_xy = True; return
    if cmd == "G91":
        state.abs_xy = False; return
    if cmd == "M82":
        state.abs_e = True; return
    if cmd == "M83":
        state.abs_e = False; return
    if cmd == "G90.1":
        state.ij_relative = False; return
    if cmd == "G91.1":
        state.ij_relative = True; return

    # --- G92: set position ---
    if cmd == "G92":
        if "X" in words: state.x = words["X"]
        if "Y" in words: state.y = words["Y"]
        if "Z" in words: state.z = words["Z"]
        if "E" in words: state.e = words["E"]
        return

    # --- G0/G1 linear move ---
    if _MOVE_RE.match(cmd):
        if state.abs_xy:
            if "X" in words: state.x = words["X"]
            if "Y" in words: state.y = words["Y"]
            if "Z" in words: state.z = words["Z"]
        else:
            state.x += words.get("X", 0.0)
            state.y += words.get("Y", 0.0)
            state.z += words.get("Z", 0.0)
        if "E" in words:
            state.e = words["E"] if state.abs_e else state.e + words["E"]
        if "F" in words:
            state.f = words["F"]
        return

    # --- G2/G3 arc move (update endpoint only; arc-centre handling not needed here) ---
    if _ARC_RE.match(cmd):
        if state.abs_xy:
            if "X" in words: state.x = words["X"]
            if "Y" in words: state.y = words["Y"]
        else:
            state.x += words.get("X", 0.0)
            state.y += words.get("Y", 0.0)
        if "E" in words:
            state.e = words["E"] if state.abs_e else state.e + words["E"]
        if "F" in words:
            state.f = words["F"]
        return

    # --- Anything else that carries an E word (e.g. bare E-only commands) ---
    if "E" in words:
        state.e = words["E"] if state.abs_e else state.e + words["E"]


def iter_with_state(
    lines: List[GCodeLine],
    initial_state: Optional[ModalState] = None,
) -> Iterator[Tuple[GCodeLine, ModalState]]:
    """Yield ``(line, state)`` for every line in *lines*.

    *state* is the :class:`ModalState` **before** the line is processed —
    i.e. the printer's mode when executing that line.  A snapshot copy is
    yielded; callers may safely retain it.
    """
    state = initial_state.copy() if initial_state else ModalState()
    for line in lines:
        yield line, state.copy()
        advance_state(state, line)


def iter_moves(
    lines: List[GCodeLine],
    initial_state: Optional[ModalState] = None,
) -> Iterator[Tuple[GCodeLine, ModalState]]:
    """Yield ``(line, state)`` for G0/G1 lines only."""
    for line, state in iter_with_state(lines, initial_state):
        if line.is_move:
            yield line, state


def iter_arcs(
    lines: List[GCodeLine],
    initial_state: Optional[ModalState] = None,
) -> Iterator[Tuple[GCodeLine, ModalState]]:
    """Yield ``(line, state)`` for G2/G3 lines only."""
    for line, state in iter_with_state(lines, initial_state):
        if line.is_arc:
            yield line, state


def iter_extruding(
    lines: List[GCodeLine],
    initial_state: Optional[ModalState] = None,
) -> Iterator[Tuple[GCodeLine, ModalState]]:
    """Yield ``(line, state)`` for lines that deposit material.

    A line is considered extruding when it is a G0/G1/G2/G3 move that
    carries an E word whose value is greater than the current accumulator
    (absolute E) or greater than zero (relative E).
    """
    for line, state in iter_with_state(lines, initial_state):
        if not (line.is_move or line.is_arc):
            continue
        if "E" not in line.words:
            continue
        e_word = line.words["E"]
        if state.abs_e:
            if e_word > state.e:
                yield line, state
        else:
            if e_word > 0.0:
                yield line, state


# ---------------------------------------------------------------------------
# Formatting utilities
# ---------------------------------------------------------------------------

def fmt_float(v: float, places: int) -> str:
    """Format *v* to *places* decimal places, trimming trailing zeros.

    >>> fmt_float(10.0, 3)
    '10'
    >>> fmt_float(10.125, 3)
    '10.125'
    >>> fmt_float(-0.0, 3)
    '0'
    """
    s = f"{v:.{places}f}".rstrip("0").rstrip(".")
    if not s or s == "-":
        return "0"
    # Negative zero: "-0" should render as "0"
    if s == "-0":
        return "0"
    return s


def fmt_axis(
    axis: str,
    v: float,
    xy_decimals: int = DEFAULT_XY_DECIMALS,
    other_decimals: int = DEFAULT_OTHER_DECIMALS,
) -> str:
    """Format an axis value with axis-appropriate precision.

    X and Y use *xy_decimals*; all other axes use *other_decimals*.
    """
    places = xy_decimals if axis.upper() in ("X", "Y") else other_decimals
    return fmt_float(v, places)


def replace_or_append(
    code: str,
    axis: str,
    val: float,
    xy_decimals: int = DEFAULT_XY_DECIMALS,
    other_decimals: int = DEFAULT_OTHER_DECIMALS,
) -> str:
    """Replace the value of *axis* in *code*, or append it if absent.

    Only the first occurrence of the axis letter is replaced.
    """
    axis = axis.upper()
    tok = f"{axis}{fmt_axis(axis, val, xy_decimals, other_decimals)}"
    pat = re.compile(rf"(?i)\b{axis}\s*({_NUM_RE})\b")
    return pat.sub(tok, code, count=1) if pat.search(code) else (code + " " + tok)


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def from_text(text: str) -> GCodeFile:
    """Create a :class:`GCodeFile` from a G-code text string."""
    return GCodeFile(
        lines=parse_lines(text),
        thumbnails=[],
        source_format="text",
    )


def to_text(gf: GCodeFile) -> str:
    """Render a :class:`GCodeFile` back to a G-code text string.

    Lines are joined with newlines and a trailing newline is appended.
    Untransformed lines preserve their original formatting via
    ``GCodeLine.raw``; transformed lines carry regenerated text.
    """
    return "\n".join(line.raw for line in gf.lines) + "\n"


def load(path: str) -> GCodeFile:
    """Load a G-code file from *path*, auto-detecting text vs binary format.

    Raises :class:`ValueError` if the file is binary but not a recognised
    Prusa .bgcode file.
    """
    if _is_bgcode_file(path):
        with open(path, "rb") as fh:
            return _load_bgcode(fh.read())
    with open(path, "rb") as fh:
        head = fh.read(512)
    if b"\x00" in head:
        raise ValueError(
            f"{path!r} appears to be binary but is not a valid .bgcode file "
            "(expected 'GCDE' magic).  Supported formats: text .gcode and Prusa .bgcode."
        )
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return GCodeFile(lines=parse_lines(text), thumbnails=[], source_format="text")


def save(gf: GCodeFile, path: str) -> None:
    """Atomically write *gf* to *path*, preserving the original file format.

    For .bgcode sources all non-GCode blocks (thumbnails, slicer/printer/
    print metadata) are preserved verbatim so the resulting file can be
    uploaded to PrusaConnect and printed directly.
    """
    d = os.path.dirname(os.path.abspath(path)) or "."
    text = to_text(gf)
    if gf.source_format == "bgcode" and gf._bgcode_file_hdr is not None:
        data = _bgcode_reassemble(
            gf._bgcode_file_hdr,
            gf._bgcode_nongcode_blocks or [],
            text,
        )
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    else:
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp", text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Binary .bgcode support (private)
# ---------------------------------------------------------------------------

def _is_bgcode_file(path: str) -> bool:
    """Return True if *path* starts with the ``GCDE`` magic bytes."""
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == _BGCODE_MAGIC
    except OSError:
        return False


def _bgcode_split(
    data: bytes,
) -> Tuple[bytes, List[bytes], List[Thumbnail], str]:
    """Parse a .bgcode byte string into its components.

    Returns
    -------
    file_hdr
        10-byte file prefix (magic + version + checksum_type).
    nongcode_blocks
        Raw bytes of every non-GCode block in original order.  These are
        suitable for verbatim re-embedding on reassembly and include
        thumbnail blocks.
    thumbnails
        Parsed :class:`Thumbnail` objects (subset of nongcode_blocks).
    gcode_text
        Concatenated UTF-8 G-code text from all GCode blocks.

    Raises :class:`ValueError` for invalid, truncated, or unsupported files.
    """
    if len(data) < 10:
        raise ValueError(f"Data too short ({len(data)} bytes) to be a .bgcode file")
    if data[:4] != _BGCODE_MAGIC:
        raise ValueError(
            f"Not a .bgcode file: expected magic {_BGCODE_MAGIC!r}, got {data[:4]!r}"
        )

    file_hdr = data[:10]
    pos = 10
    gcode_parts: List[str] = []
    nongcode_raws: List[bytes] = []
    thumbnails: List[Thumbnail] = []

    while pos < len(data):
        block_start = pos
        if pos + 8 > len(data):
            raise ValueError(f"Truncated block header at offset {pos}")

        btype, comp = struct.unpack_from("<HH", data, pos)
        uncomp_size, = struct.unpack_from("<I", data, pos + 4)

        if comp == _COMP_NONE:
            comp_size = uncomp_size
            hdr_len = 8
        elif comp in (_COMP_DEFLATE, 2, 3):  # DEFLATE or Heatshrink variants
            if pos + 12 > len(data):
                raise ValueError(f"Truncated compressed block header at offset {pos}")
            comp_size, = struct.unpack_from("<I", data, pos + 8)
            hdr_len = 12
        else:
            raise ValueError(f"Unknown compression type {comp} at offset {pos}")

        params_len = 6 if btype == _BLK_THUMBNAIL else 2
        params_start = pos + hdr_len
        payload_start = params_start + params_len
        payload_end = payload_start + comp_size

        if payload_end + 4 > len(data):
            raise ValueError(
                f"Truncated block payload at offset {pos}: "
                f"need {payload_end + 4 - len(data)} more bytes"
            )

        stored_crc, = struct.unpack_from("<I", data, payload_end)
        computed_crc = zlib.crc32(data[pos:payload_end]) & 0xFFFFFFFF
        if computed_crc != stored_crc:
            raise ValueError(
                f"CRC32 mismatch in block type {btype} at offset {pos}: "
                f"computed {computed_crc:#010x}, stored {stored_crc:#010x}"
            )

        block_end = payload_end + 4
        raw_block = data[block_start:block_end]
        payload = data[payload_start:payload_end]

        if btype == _BLK_GCODE:
            enc, = struct.unpack_from("<H", data, params_start)
            if comp == _COMP_NONE:
                raw_payload = payload
            elif comp == _COMP_DEFLATE:
                try:
                    raw_payload = zlib.decompress(payload)
                except zlib.error as exc:
                    raise ValueError(
                        f"DEFLATE decompression failed at offset {pos}: {exc}"
                    ) from exc
            else:
                raise ValueError(
                    f"Unsupported GCode block compression {comp} at offset {pos} "
                    "(Heatshrink decoding requires a third-party library)"
                )
            if enc != _ENC_RAW:
                raise ValueError(
                    f"Unsupported GCode block encoding {enc} at offset {pos} "
                    "(MeatPack decoding is not supported)"
                )
            gcode_parts.append(raw_payload.decode("utf-8"))

        elif btype == _BLK_THUMBNAIL:
            params_bytes = data[params_start:payload_start]
            if comp == _COMP_NONE:
                img_data = payload
            elif comp == _COMP_DEFLATE:
                try:
                    img_data = zlib.decompress(payload)
                except zlib.error as exc:
                    raise ValueError(
                        f"DEFLATE decompression of thumbnail failed at offset {pos}: {exc}"
                    ) from exc
            else:
                img_data = payload  # store as-is for unsupported compression
            thumbnails.append(Thumbnail(params=params_bytes, data=img_data, _raw_block=raw_block))
            nongcode_raws.append(raw_block)

        else:
            nongcode_raws.append(raw_block)

        pos = block_end

    if not gcode_parts:
        raise ValueError("No GCode blocks found in .bgcode data")

    return file_hdr, nongcode_raws, thumbnails, "".join(gcode_parts)


def _bgcode_reassemble(
    file_hdr: bytes,
    nongcode_blocks: List[bytes],
    gcode_text: str,
) -> bytes:
    """Rebuild a .bgcode byte string from its components.

    A single new GCode block (COMP_NONE, ENC_RAW) is appended after all
    non-GCode blocks, matching the libbgcode convention (metadata before
    G-code).
    """
    payload = gcode_text.encode("utf-8")
    hdr    = struct.pack("<HHI", _BLK_GCODE, _COMP_NONE, len(payload))
    params = struct.pack("<H", _ENC_RAW)
    cksum  = zlib.crc32(hdr) & 0xFFFFFFFF
    cksum  = zlib.crc32(params, cksum) & 0xFFFFFFFF
    cksum  = zlib.crc32(payload, cksum) & 0xFFFFFFFF
    gcode_block = hdr + params + payload + struct.pack("<I", cksum)
    return file_hdr + b"".join(nongcode_blocks) + gcode_block


def _load_bgcode(data: bytes) -> GCodeFile:
    """Create a :class:`GCodeFile` from raw .bgcode bytes."""
    file_hdr, nongcode_blocks, thumbnails, gcode_text = _bgcode_split(data)
    return GCodeFile(
        lines=parse_lines(gcode_text),
        thumbnails=thumbnails,
        source_format="bgcode",
        _bgcode_file_hdr=file_hdr,
        _bgcode_nongcode_blocks=nongcode_blocks,
    )


# ---------------------------------------------------------------------------
# Arc geometry helpers
# ---------------------------------------------------------------------------

def _arc_center(state: ModalState, words: Dict[str, float]) -> Tuple[float, float]:
    """Compute the absolute arc centre from I/J words and modal state."""
    I = words.get("I", 0.0)
    J = words.get("J", 0.0)
    if state.ij_relative:
        return (state.x + I, state.y + J)
    return (I, J)


def _arc_end_abs(state: ModalState, words: Dict[str, float]) -> Tuple[float, float]:
    """Compute the absolute arc endpoint from words and modal state."""
    if state.abs_xy:
        return words.get("X", state.x), words.get("Y", state.y)
    return state.x + words.get("X", 0.0), state.y + words.get("Y", 0.0)


def _sweep_angle(a0: float, a1: float, cw: bool) -> float:
    """Compute the signed sweep angle from *a0* to *a1* for CW or CCW arcs."""
    da = a1 - a0
    while da <= -math.pi: da += 2 * math.pi
    while da >   math.pi: da -= 2 * math.pi
    if cw and da > 0:     da -= 2 * math.pi
    if not cw and da < 0: da += 2 * math.pi
    return da


def linearize_arc_points(
    state: ModalState,
    words: Dict[str, float],
    cw: bool,
    seg_mm: float = DEFAULT_ARC_SEG_MM,
    max_deg: float = DEFAULT_ARC_MAX_DEG,
) -> List[Tuple[float, float]]:
    """Convert a G2/G3 arc into a list of ``(x, y)`` interpolated points.

    The last point is exactly the arc endpoint.  Returns at least one point.

    Parameters
    ----------
    state:   Current modal state (provides start position and IJ mode).
    words:   Parsed axis words for the arc command.
    cw:      True for G2 (clockwise), False for G3 (counter-clockwise).
    seg_mm:  Maximum chord length (mm) per segment.
    max_deg: Maximum sweep angle (degrees) per segment.
    """
    x0, y0 = state.x, state.y
    x1, y1 = _arc_end_abs(state, words)
    cx, cy = _arc_center(state, words)

    r0 = math.hypot(x0 - cx, y0 - cy)
    r1 = math.hypot(x1 - cx, y1 - cy)
    r  = 0.5 * (r0 + r1) if (r0 > 0 and r1 > 0) else max(r0, r1)

    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    da = _sweep_angle(a0, a1, cw=cw)

    # Full-circle arc: start == end gives da ≈ 0; detect by non-zero radius
    if abs(da) < EPS and r > EPS:
        da = -2 * math.pi if cw else 2 * math.pi

    arc_len = abs(da) * r if r > 0 else math.hypot(x1 - x0, y1 - y0)
    max_rad = math.radians(max_deg) if max_deg > 0 else abs(da)
    steps_len = int(math.ceil(arc_len / max(seg_mm, 1e-6))) if seg_mm > 0 else 1
    steps_ang = int(math.ceil(abs(da) / max(max_rad, 1e-9))) if max_deg > 0 else 1
    steps = max(1, steps_len, steps_ang)

    pts: List[Tuple[float, float]] = []
    for i in range(1, steps + 1):
        t  = i / steps
        ai = a0 + da * t
        xi = cx + r * math.cos(ai)
        yi = cy + r * math.sin(ai)
        if i == steps:
            xi, yi = x1, y1
        pts.append((xi, yi))
    return pts


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------

def linearize_arcs(
    lines: List[GCodeLine],
    seg_mm: float = DEFAULT_ARC_SEG_MM,
    max_deg: float = DEFAULT_ARC_MAX_DEG,
    xy_decimals: int = DEFAULT_XY_DECIMALS,
    other_decimals: int = DEFAULT_OTHER_DECIMALS,
    initial_state: Optional[ModalState] = None,
) -> List[GCodeLine]:
    """Replace all G2/G3 arcs with equivalent G1 segments.

    E is distributed proportionally across segments; feedrate and comment
    are preserved on the first emitted segment only.  Returns a new list;
    the input is unchanged.
    """
    result: List[GCodeLine] = []
    state = initial_state.copy() if initial_state else ModalState()

    for line in lines:
        if not line.is_arc:
            result.append(line)
            advance_state(state, line)
            continue

        words = line.words
        cw  = (line.command.upper() == "G2")
        pts = linearize_arc_points(state, words, cw, seg_mm, max_deg)
        n   = len(pts)

        has_e = "E" in words
        e0    = state.e
        if has_e:
            if state.abs_e:
                e_end = words["E"]
                dE    = e_end - e0
            else:
                dE    = words["E"]
                e_end = e0 + dE
        else:
            dE    = 0.0
            e_end = e0

        has_f  = "F" in words
        f_word = words.get("F")

        # Per-segment E for relative extrusion: distribute dE across segments
        # in output precision and close out on the last segment.
        e_accum = 0.0
        per_e   = round(dE / n, other_decimals) if (has_e and not state.abs_e and n > 0) else 0.0

        for i, (xi, yi) in enumerate(pts, start=1):
            parts = [
                "G1",
                f"X{fmt_axis('X', xi, xy_decimals, other_decimals)}",
                f"Y{fmt_axis('Y', yi, xy_decimals, other_decimals)}",
            ]

            if has_e:
                if state.abs_e:
                    t  = i / n
                    ei = (e0 + dE * t) if i < n else e_end
                else:
                    if i < n:
                        ei       = per_e
                        e_accum += ei
                    else:
                        ei = round(dE - e_accum, other_decimals)
                parts.append(f"E{fmt_axis('E', ei, xy_decimals, other_decimals)}")

            if has_f and f_word is not None and i == 1:
                parts.append(f"F{fmt_axis('F', f_word, xy_decimals, other_decimals)}")

            raw = " ".join(parts)
            seg_comment = (line.comment if i == 1 else "")
            if seg_comment:
                raw += " " + seg_comment.lstrip()

            new_words: Dict[str, float] = {"X": xi, "Y": yi}
            if has_e:
                new_words["E"] = ei  # type: ignore[possibly-undefined]
            if has_f and f_word is not None and i == 1:
                new_words["F"] = f_word

            result.append(GCodeLine(
                raw=raw,
                command="G1",
                words=new_words,
                comment=seg_comment,
            ))

        # Advance state to arc endpoint
        if state.abs_xy:
            state.x = words.get("X", state.x)
            state.y = words.get("Y", state.y)
        else:
            state.x += words.get("X", 0.0)
            state.y += words.get("Y", 0.0)
        if has_e:
            state.e = e_end
        if has_f and f_word is not None:
            state.f = f_word

    return result


def apply_xy_transform(
    lines: List[GCodeLine],
    fn: Callable[[float, float], Tuple[float, float]],
    *,
    xy_decimals: int = DEFAULT_XY_DECIMALS,
    other_decimals: int = DEFAULT_OTHER_DECIMALS,
    initial_state: Optional[ModalState] = None,
) -> List[GCodeLine]:
    """Apply an arbitrary XY transform to all G0/G1 move endpoints.

    *fn* receives ``(x, y)`` in original absolute coordinates and returns
    ``(new_x, new_y)``.  Non-move lines are passed through unchanged.

    Arcs (G2/G3) are **not** transformed — call :func:`linearize_arcs`
    first when the input may contain arc commands.

    Modal state is tracked internally; position updates use the **original**
    (pre-transform) coordinates so that relative moves and arc centres
    continue to function correctly for any downstream operations.

    Raises :class:`ValueError` when a move with X/Y words is encountered
    in relative XY mode (G91), as the transform requires absolute coords.
    """
    result: List[GCodeLine] = []
    state = initial_state.copy() if initial_state else ModalState()

    for line in lines:
        if line.is_move and ("X" in line.words or "Y" in line.words):
            if not state.abs_xy:
                raise ValueError(
                    "apply_xy_transform: relative XY (G91) is not supported; "
                    "ensure G90 is active or pre-process moves to absolute coords."
                )
            x_t = line.words.get("X", state.x)
            y_t = line.words.get("Y", state.y)
            xs, ys = fn(x_t, y_t)

            code, comment = split_comment(line.raw)
            new_code = replace_or_append(code, "X", xs, xy_decimals, other_decimals)
            new_code = replace_or_append(new_code, "Y", ys, xy_decimals, other_decimals)
            for ax in ("Z", "E", "F"):
                if ax in line.words:
                    new_code = replace_or_append(
                        new_code, ax, line.words[ax], xy_decimals, other_decimals
                    )

            new_raw = new_code.rstrip() + ("" if not comment else " " + comment.lstrip())
            new_words = dict(line.words)
            new_words["X"] = xs
            new_words["Y"] = ys

            result.append(GCodeLine(
                raw=new_raw,
                command=line.command,
                words=new_words,
                comment=comment,
            ))

            # State tracks original (untransformed) position
            state.x = x_t
            state.y = y_t
            if "Z" in line.words:
                state.z = line.words["Z"] if state.abs_xy else state.z + line.words["Z"]
            if "E" in line.words:
                state.e = (line.words["E"] if state.abs_e
                           else state.e + line.words["E"])
            if "F" in line.words:
                state.f = line.words["F"]
        else:
            result.append(line)
            advance_state(state, line)

    return result


def apply_skew(
    lines: List[GCodeLine],
    skew_deg: float,
    y_ref: float = 0.0,
    *,
    xy_decimals: int = DEFAULT_XY_DECIMALS,
    other_decimals: int = DEFAULT_OTHER_DECIMALS,
    initial_state: Optional[ModalState] = None,
) -> List[GCodeLine]:
    """Apply XY skew correction to all G0/G1 move endpoints.

    Transform model (Marlin M852-compatible)::

        x' = x + (y - y_ref) * tan(skew_deg)
        y' = y

    Arcs must be linearized before calling this function — call
    :func:`linearize_arcs` first if the G-code contains G2/G3 commands.

    Parameters
    ----------
    skew_deg: Skew angle in degrees.
    y_ref:    Y reference for the shear pivot; reduces displacement for
              prints centred away from Y = 0.
    """
    k = math.tan(math.radians(skew_deg))
    return apply_xy_transform(
        lines,
        lambda x, y: (x + (y - y_ref) * k, y),
        xy_decimals=xy_decimals,
        other_decimals=other_decimals,
        initial_state=initial_state,
    )


def translate_xy(
    lines: List[GCodeLine],
    dx: float,
    dy: float,
    *,
    xy_decimals: int = DEFAULT_XY_DECIMALS,
    other_decimals: int = DEFAULT_OTHER_DECIMALS,
    initial_state: Optional[ModalState] = None,
) -> List[GCodeLine]:
    """Translate all G0/G1 XY move endpoints by ``(dx, dy)``."""
    return apply_xy_transform(
        lines,
        lambda x, y: (x + dx, y + dy),
        xy_decimals=xy_decimals,
        other_decimals=other_decimals,
        initial_state=initial_state,
    )


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def compute_bounds(
    lines: List[GCodeLine],
    *,
    extruding_only: bool = False,
    include_arcs: bool = True,
    arc_seg_mm: float = DEFAULT_ARC_SEG_MM,
    arc_max_deg: float = DEFAULT_ARC_MAX_DEG,
    initial_state: Optional[ModalState] = None,
) -> Bounds:
    """Compute XY/Z bounding box from a sequence of G-code lines.

    Parameters
    ----------
    extruding_only: Include only endpoints/points from extruding moves.
    include_arcs:   Linearize G2/G3 arcs and include their sample points.
    """
    bounds = Bounds()
    state  = initial_state.copy() if initial_state else ModalState()

    for line in lines:
        if line.is_move:
            if state.abs_xy:
                x1 = line.words.get("X", state.x)
                y1 = line.words.get("Y", state.y)
                z1 = line.words.get("Z", state.z)
            else:
                x1 = state.x + line.words.get("X", 0.0)
                y1 = state.y + line.words.get("Y", 0.0)
                z1 = state.z + line.words.get("Z", 0.0)

            if "X" in line.words or "Y" in line.words:
                is_ext = False
                if "E" in line.words:
                    e_w    = line.words["E"]
                    is_ext = (e_w > state.e) if state.abs_e else (e_w > 0.0)
                if not extruding_only or is_ext:
                    bounds.expand(x1, y1)

            if "Z" in line.words:
                bounds.expand_z(z1)

            advance_state(state, line)

        elif line.is_arc and include_arcs:
            words  = line.words
            cw     = (line.command.upper() == "G2")
            pts    = linearize_arc_points(state, words, cw, arc_seg_mm, arc_max_deg)
            is_ext = False
            if "E" in words:
                e_w    = words["E"]
                is_ext = (e_w > state.e) if state.abs_e else (e_w > 0.0)
            if not extruding_only or is_ext:
                for xi, yi in pts:
                    bounds.expand(xi, yi)
            advance_state(state, line)

        else:
            advance_state(state, line)

    return bounds


def compute_stats(
    lines: List[GCodeLine],
    initial_state: Optional[ModalState] = None,
) -> GCodeStats:
    """Compute a statistical summary of a G-code line sequence.

    Returns a :class:`GCodeStats` with move counts, bounds, layer heights,
    feedrate list, and total extrusion.
    """
    stats    = GCodeStats()
    state    = initial_state.copy() if initial_state else ModalState()
    seen_z:  List[float] = []
    seen_f:  List[float] = []
    seen_f_set: set = set()   # for O(1) global uniqueness check
    last_z:  Optional[float] = None

    for line in lines:
        stats.total_lines += 1

        if line.is_blank:
            stats.blank_lines += 1
            advance_state(state, line)
            continue

        if not line.command and line.comment:
            stats.comment_only_lines += 1
            advance_state(state, line)
            continue

        if line.is_move:
            stats.move_count += 1
            words = line.words

            if state.abs_xy:
                x1 = words.get("X", state.x)
                y1 = words.get("Y", state.y)
                z1 = words.get("Z", state.z)
            else:
                x1 = state.x + words.get("X", 0.0)
                y1 = state.y + words.get("Y", 0.0)
                z1 = state.z + words.get("Z", 0.0)

            if "X" in words or "Y" in words:
                stats.bounds.expand(x1, y1)

            if "Z" in words:
                stats.bounds.expand_z(z1)
                if last_z is None or abs(z1 - last_z) > EPS:
                    seen_z.append(z1)
                    last_z = z1

            if "F" in words:
                f = words["F"]
                if f not in seen_f_set:
                    seen_f.append(f)
                    seen_f_set.add(f)

            is_ext = False
            if "E" in words:
                e_word = words["E"]
                if state.abs_e:
                    dE     = e_word - state.e
                    is_ext = dE > 0
                    if is_ext:
                        stats.total_extrusion += dE
                    elif dE < -EPS:
                        stats.retract_count += 1
                else:
                    is_ext = e_word > 0.0
                    if is_ext:
                        stats.total_extrusion += e_word
                    elif e_word < -EPS:
                        stats.retract_count += 1

            if is_ext:
                stats.extrude_count += 1
            else:
                stats.travel_count += 1

        elif line.is_arc:
            stats.arc_count += 1
            words = line.words
            cw    = (line.command.upper() == "G2")
            pts   = linearize_arc_points(state, words, cw)

            for xi, yi in pts:
                stats.bounds.expand(xi, yi)

            if "F" in words:
                f = words["F"]
                if f not in seen_f_set:
                    seen_f.append(f)
                    seen_f_set.add(f)

            is_ext = False
            if "E" in words:
                e_word = words["E"]
                if state.abs_e:
                    dE     = e_word - state.e
                    is_ext = dE > 0
                    if is_ext:
                        stats.total_extrusion += dE
                    elif dE < -EPS:
                        stats.retract_count += 1
                else:
                    is_ext = e_word > 0.0
                    if is_ext:
                        stats.total_extrusion += e_word
                    elif e_word < -EPS:
                        stats.retract_count += 1

            if is_ext:
                stats.extrude_count += 1
            else:
                stats.travel_count += 1

        advance_state(state, line)

    stats.z_heights = seen_z
    stats.feedrates  = seen_f
    return stats
