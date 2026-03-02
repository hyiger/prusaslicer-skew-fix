#!/usr/bin/env python3
"""
prusaslicer-skew-fix

PrusaSlicer post-processing hook that applies XY skew correction to G-code.
Supports both plain text .gcode and Prusa binary .bgcode files.

Skew model (shear):
    x' = x + y * tan(theta)
    y' = y

Key features:
- Optional arc linearization (G2/G3 -> G1) for mathematically correct skew
- Optional auto-recenter + bounds check to prevent clipping
- Bounds/recenter are computed from **model-space extrusion moves** only:
  - Only moves that EXTRUDE (E increases / E>0) are included
  - Only endpoints that are already IN-BED in the original G-code are included
  - Purge/wipe/parking moves outside the bed do not affect recentering

Binary G-code (.bgcode) support:
- Detected by the 'GCDE' magic header.
- G-code text is decoded from GCode blocks, corrected, then re-encoded in place.
- All non-GCode blocks (thumbnails, slicer/printer/print metadata) are preserved
  unchanged so the corrected file can be uploaded to PrusaConnect or printed directly.
- Supports COMP_NONE and COMP_DEFLATE payloads; ENC_RAW (UTF-8) encoding only.
  Heatshrink and MeatPack variants are rejected with a clear error.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import struct
import sys
import tempfile
import zlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# ---- Correctness-locked constants ----
ARC_SEG_MM = 0.20
ARC_MAX_DEG = 5.0
EPS = 1e-9
XY_DECIMALS = 3
OTHER_DECIMALS = 5



MOVE_RE = re.compile(r"^(G0|G1)\b", re.IGNORECASE)
ARC_RE  = re.compile(r"^(G2|G3)\b", re.IGNORECASE)
NUM_RE = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
AXIS_RE = re.compile(rf"([XYZEFRIJK])\s*({NUM_RE})", re.IGNORECASE)


def _handle_modal_state_line(st: "State", up: str) -> bool:
    """Update modal state from a stripped, uppercased G-code line.

    Returns True if the line was a recognized modal command that only changes
    state (and should generally be passed through unchanged), otherwise False.
    """
    parts = up.split(None, 1)
    if not parts:
        return False
    cmd = parts[0]
    if cmd == "G90.1":
        st.ij_relative = False
        return True
    if cmd == "G91.1":
        st.ij_relative = True
        return True
    if cmd == "G90":
        st.abs_xy = True
        return True
    if cmd == "G91":
        st.abs_xy = False
        return True
    if cmd == "M82":
        st.abs_e = True
        return True
    if cmd == "M83":
        st.abs_e = False
        return True
    return False

@dataclass
class State:
    abs_xy: bool = True          # G90/G91
    abs_e: bool = True           # M82/M83
    ij_relative: bool = True     # G91.1 relative IJK; G90.1 absolute IJK
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    e: float = 0.0
    f: Optional[float] = None

# Split a G-code line into code and ';' comment parts.
def split_comment(line: str) -> Tuple[str, str]:
    if ";" in line:
        code, comment = line.split(";", 1)
        return code.rstrip(), ";" + comment
    return line.rstrip(), ""

# Parse axis words (X/Y/Z/E/F/I/J/K) from a G-code command.
def parse_words(code: str) -> Dict[str, float]:
    return {m.group(1).upper(): float(m.group(2)) for m in AXIS_RE.finditer(code)}

# Format float values compactly for G-code output (trim trailing zeros).
def _fmt_fixed(v: float, places: int) -> str:
    s = f"{v:.{places}f}".rstrip("0").rstrip(".")
    return s if s else "0"

def fmt_axis(axis: str, v: float, xy_places: int, other_places: int) -> str:
    """Format a value for a specific axis.

    - X/Y use xy_places (often 3 is plenty)
    - Everything else (E/F/I/J/K/Z/...) uses other_places (default 5)
    """
    a = axis.upper()
    places = xy_places if a in ("X", "Y") else other_places
    return _fmt_fixed(v, places)


# Replace an axis value in a G-code line, or append it if missing.
def replace_or_append(code: str, axis: str, val: float, *, xy_places: int, other_places: int) -> str:
    """Replace an axis value in a G-code line, or append it if missing."""
    axis = axis.upper()
    tok = f"{axis}{fmt_axis(axis, val, xy_places, other_places)}"
    pat = re.compile(rf"(?i)\b{axis}\s*({NUM_RE})\b")
    return pat.sub(tok, code, 1) if pat.search(code) else (code + " " + tok)

# Abort if the input file is binary (NUL bytes) and is NOT a recognised bgcode.
# Called only after _is_bgcode() has returned False, so GCDE files never reach here.
def _assert_text_gcode(path: str) -> None:
    with open(path, "rb") as f:
        head = f.read(512)
    if b"\x00" in head:
        raise SystemExit(
            "prusaslicer-skew-fix: ERROR: File appears to be binary (NUL bytes detected).\n"
            "Supported formats: text .gcode and Prusa binary .bgcode (magic 'GCDE').\n"
            "If using PrusaSlicer with binary output, verify the file is a valid .bgcode."
        )


def _is_bgcode(path: str) -> bool:
    """Return True if the file is a Prusa binary G-code file (magic 'GCDE')."""
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"GCDE"
    except OSError:
        return False

# Apply XY shear transform: x' = x + y*tan(theta), y' = y (Marlin M852-compatible).
def apply_skew_abs(x: float, y: float, k: float, y_ref: float = 0.0) -> Tuple[float, float]:
    """Apply XY shear/skew in absolute coordinates.

    Model:
        x' = x + (y - y_ref) * k
        y' = y

    Using a non-zero y_ref recenters the shear reference so the X displacement
    is symmetric about y_ref (often the model's Y center).
    """
    return (x + (y - y_ref) * k, y)

# Compute the absolute arc center from I/J (supports G90.1/G91.1).
def _arc_center(st: State, words: Dict[str, float]) -> Tuple[float, float]:
    I = words.get("I", 0.0)
    J = words.get("J", 0.0)
    if st.ij_relative:
        return (st.x + I, st.y + J)
    return (I, J)

# Compute the absolute end XY for an arc (respects G90/G91 state).
def _arc_end_abs(st: State, words: Dict[str, float]) -> Tuple[float, float]:
    if st.abs_xy:
        x1 = words.get("X", st.x)
        y1 = words.get("Y", st.y)
    else:
        x1 = st.x + words.get("X", 0.0)
        y1 = st.y + words.get("Y", 0.0)
    return x1, y1

# Compute signed sweep angle for CW/CCW arcs, normalized for interpolation.
def _sweep(a0: float, a1: float, cw: bool) -> float:
    da = a1 - a0
    while da <= -math.pi:
        da += 2 * math.pi
    while da > math.pi:
        da -= 2 * math.pi
    if cw:
        if da > 0:
            da -= 2 * math.pi
    else:
        if da < 0:
            da += 2 * math.pi
    return da

# Linearize a single G2/G3 arc into G1 points before skewing (circles → ellipses under shear).
def linearize_arc_points(st: State, words: Dict[str, float], cw: bool,
                         seg_mm: float, max_deg: float) -> List[Tuple[float, float]]:
    x0, y0 = st.x, st.y
    x1, y1 = _arc_end_abs(st, words)
    cx, cy = _arc_center(st, words)

    r0 = math.hypot(x0 - cx, y0 - cy)
    r1 = math.hypot(x1 - cx, y1 - cy)
    r = 0.5 * (r0 + r1) if (r0 > 0 and r1 > 0) else max(r0, r1)

    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)

    da = _sweep(a0, a1, cw=cw)
    # A full-circle arc has start == end, so _sweep returns 0. Detect this by
    # checking for nonzero radius with a near-zero sweep and treat as ±2π.
    if abs(da) < EPS and r > EPS:
        da = -2 * math.pi if cw else 2 * math.pi
    arc_len = abs(da) * r if r > 0 else math.hypot(x1 - x0, y1 - y0)

    max_rad = math.radians(max_deg) if max_deg > 0 else abs(da)
    steps_len = int(math.ceil(arc_len / max(seg_mm, 1e-6))) if seg_mm > 0 else 1
    steps_ang = int(math.ceil(abs(da) / max(max_rad, 1e-9))) if max_deg > 0 else 1
    steps = max(1, steps_len, steps_ang)

    pts: List[Tuple[float, float]] = []
    for i in range(1, steps + 1):
        t = i / steps
        ai = a0 + da * t
        xi = cx + r * math.cos(ai)
        yi = cy + r * math.sin(ai)
        if i == steps:
            xi, yi = x1, y1
        pts.append((xi, yi))
    return pts

# Return True if an (x, y) point lies within the printable bed rectangle.
def _in_bed(x: float, y: float, xmin: float, xmax: float, ymin: float, ymax: float) -> bool:
    return (xmin <= x <= xmax) and (ymin <= y <= ymax)

# Return True if a move deposits plastic based on current extrusion mode/state.
def _is_extruding_move(st: State, words: Dict[str, float]) -> bool:
    if "E" not in words:
        return False
    e_word = words["E"]
    if st.abs_e:
        return e_word > st.e
    return e_word > 0.0

# Choose dx/dy using 'center' or minimal-shift 'clamp' strategy.
def _choose_translation(lo: float, hi: float, mode: str) -> float:
    if mode == "center":
        return 0.5 * (lo + hi)
    if lo <= 0.0 <= hi:
        return 0.0
    return lo if abs(lo) < abs(hi) else hi


def _parse_csv_floats(spec: str, count: int, name: str) -> Tuple[float, ...]:
    parts = [p.strip() for p in spec.split(",")]
    if len(parts) != count:
        raise ValueError(f"{name} expects {count} comma-separated numbers.")
    try:
        vals = tuple(float(p) for p in parts)
    except ValueError as exc:
        raise ValueError(f"{name} contains a non-numeric value.") from exc
    if any(not math.isfinite(v) for v in vals):
        raise ValueError(f"{name} contains a non-finite value.")
    return vals


def _non_negative_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid non-negative int value: '{text}'") from exc
    if value < 0:
        raise argparse.ArgumentTypeError(f"invalid non-negative int value: '{text}'")
    return value


def _non_negative_float(text: str) -> float:
    value = _finite_float(text)
    if value < 0.0:
        raise argparse.ArgumentTypeError(f"invalid non-negative float value: '{text}'")
    return value


def _finite_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid float value: '{text}'") from exc
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(f"invalid finite float value: '{text}'")
    return value


def skew_deg_from_square(spec: str) -> float:
    """Derive skew angle from square measurements: AC,BD,AD.

    Exact formula (no small-angle approximation):
        k = (AC² - BD²) / (4 · AD²)
    """
    ac, bd, ad = _parse_csv_floats(spec, 3, "--skew-from-square")
    if ad == 0.0:
        raise ValueError("--skew-from-square: AD must be non-zero.")
    k = (ac * ac - bd * bd) / (4.0 * ad * ad)
    return math.degrees(math.atan(k))


def skew_deg_from_rectangle(spec: str) -> float:
    """Derive skew angle from rectangle measurements: AC,BD,AD,AB.

    Exact formula (no small-angle approximation):
        k = (AC² - BD²) / (4 · AB · AD)
    """
    ac, bd, ad, ab = _parse_csv_floats(spec, 4, "--skew-from-rectangle")
    if ad == 0.0:
        raise ValueError("--skew-from-rectangle: AD must be non-zero.")
    if ab == 0.0:
        raise ValueError("--skew-from-rectangle: AB must be non-zero.")
    k = (ac * ac - bd * bd) / (4.0 * ab * ad)
    return math.degrees(math.atan(k))

def _scan_inbed_bounds(
    path: str,
    *,
    bed_x_min: float,
    bed_x_max: float,
    bed_y_min: float,
    bed_y_max: float,
    require_abs_xy: bool,
    move_point_fn,
) -> Tuple[float, float, float, float, bool]:
    """Scan G-code and collect in-bed bounds used for recenter calculations.

    - Arc points are included only for extruding arcs and only if in-bed.
    - G0/G1 endpoints are included only for extruding moves that end in-bed.
    - move_point_fn maps eligible move endpoints before bounds update.
    """
    st = State()
    minx = float("inf"); maxx = float("-inf")
    miny = float("inf"); maxy = float("-inf")

    def upd(x: float, y: float) -> None:
        nonlocal minx, maxx, miny, maxy
        minx = min(minx, x); maxx = max(maxx, x)
        miny = min(miny, y); maxy = max(maxy, y)

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            code, _ = split_comment(line)
            s = code.strip()
            if not s:
                continue
            up = s.upper()

            if _handle_modal_state_line(st, up):
                continue
            if require_abs_xy and not st.abs_xy:
                raise SystemExit("prusaslicer-skew-fix: ERROR: --recenter-to-bed requires absolute XY (G90).")

            if ARC_RE.match(s):
                words = parse_words(code)
                extruding = _is_extruding_move(st, words)
                cw = (s.split()[0].upper() == "G2")
                pts = linearize_arc_points(st, words, cw=cw, seg_mm=ARC_SEG_MM, max_deg=ARC_MAX_DEG)
                if extruding:
                    for (xi, yi) in pts:
                        if _in_bed(xi, yi, bed_x_min, bed_x_max, bed_y_min, bed_y_max):
                            xb, yb = move_point_fn(xi, yi)
                            upd(xb, yb)
                st.x, st.y = pts[-1]
                if "E" in words:
                    st.e = words["E"] if st.abs_e else (st.e + words["E"])
                if "F" in words:
                    st.f = words["F"]
                continue

            if MOVE_RE.match(s):
                words = parse_words(code)
                if st.abs_xy:
                    x1 = words.get("X", st.x)
                    y1 = words.get("Y", st.y)
                else:
                    x1 = st.x + words.get("X", 0.0)
                    y1 = st.y + words.get("Y", 0.0)
                if _is_extruding_move(st, words) and _in_bed(x1, y1, bed_x_min, bed_x_max, bed_y_min, bed_y_max):
                    xb, yb = move_point_fn(x1, y1)
                    upd(xb, yb)
                st.x, st.y = x1, y1
                if "E" in words:
                    st.e = words["E"] if st.abs_e else (st.e + words["E"])
                continue

            if "E" in up:
                words = parse_words(code)
                if "E" in words:
                    st.e = words["E"] if st.abs_e else (st.e + words["E"])

    if minx == float("inf"):
        return (0.0, 0.0, 0.0, 0.0, False)
    return (minx, maxx, miny, maxy, True)


# Compute original (unskewed) in-bed extruding bounds (endpoints only for G0/G1, optional linearized arc points).
def compute_inbed_extruding_bounds_original(path: str,bed_x_min: float, bed_x_max: float,
                                           bed_y_min: float, bed_y_max: float) -> Tuple[float,float,float,float]:
    minx, maxx, miny, maxy, _ = _scan_inbed_bounds(
        path,
        bed_x_min=bed_x_min,
        bed_x_max=bed_x_max,
        bed_y_min=bed_y_min,
        bed_y_max=bed_y_max,
        require_abs_xy=False,
        move_point_fn=lambda x, y: (x, y),
    )
    return minx, maxx, miny, maxy

# Compute dx/dy so skewed extruding in-bed geometry stays within the bed.
def compute_translation_for_bounds(path: str, k: float, y_ref: float,bed_x_min: float, bed_x_max: float,
                                  bed_y_min: float, bed_y_max: float,
                                  margin: float,
                                  recenter_mode: str) -> Tuple[float, float, Tuple[float,float,float,float]]:
    eps = EPS
    minx, maxx, miny, maxy, has_points = _scan_inbed_bounds(
        path,
        bed_x_min=bed_x_min,
        bed_x_max=bed_x_max,
        bed_y_min=bed_y_min,
        bed_y_max=bed_y_max,
        require_abs_xy=True,
        move_point_fn=lambda x, y: apply_skew_abs(x, y, k, y_ref),
    )

    if not has_points:
        return 0.0, 0.0, (0.0, 0.0, 0.0, 0.0)

    dx_lo = (bed_x_min + margin) - minx
    dx_hi = (bed_x_max - margin) - maxx
    dy_lo = (bed_y_min + margin) - miny
    dy_hi = (bed_y_max - margin) - maxy

    if (dx_lo - dx_hi) > eps or (dy_lo - dy_hi) > eps:
        raise SystemExit(
            "prusaslicer-skew-fix: ERROR: Model geometry cannot fit within bed after skew.\n"
            f"Skewed in-bed extruding bounds: X[{minx:.3f}, {maxx:.3f}] Y[{miny:.3f}, {maxy:.3f}]\n"
            f"Bed bounds: X[{bed_x_min:.3f}, {bed_x_max:.3f}] "
            f"Y[{bed_y_min:.3f}, {bed_y_max:.3f}] (margin {margin:.3f})"
        )

    dx = _choose_translation(dx_lo, dx_hi, recenter_mode)
    dy = _choose_translation(dy_lo, dy_hi, recenter_mode)
    return dx, dy, (minx, maxx, miny, maxy)


def analyze_gcode(path: str, k: float, y_ref: float,bed_x_min: float, bed_x_max: float, bed_y_min: float, bed_y_max: float,
                 recenter: bool, margin: float, recenter_mode: str) -> List[str]:
    """Analyze the effect of skew (and optional recenter) without rewriting the file."""
    _assert_text_gcode(path)

    dx = dy = 0.0
    skew_bounds = (0.0, 0.0, 0.0, 0.0)
    if recenter:
        dx, dy, skew_bounds = compute_translation_for_bounds(
            path, k, y_ref,
            bed_x_min, bed_x_max, bed_y_min, bed_y_max, margin,
            recenter_mode,
        )

    minx0 = float("inf"); maxx0 = float("-inf")
    miny0 = float("inf"); maxy0 = float("-inf")
    minx1 = float("inf"); maxx1 = float("-inf")
    miny1 = float("inf"); maxy1 = float("-inf")
    max_abs_dx = 0.0

    def upd0(x: float, y: float):
        nonlocal minx0, maxx0, miny0, maxy0
        minx0 = min(minx0, x); maxx0 = max(maxx0, x)
        miny0 = min(miny0, y); maxy0 = max(maxy0, y)

    def upd1(x: float, y: float):
        nonlocal minx1, maxx1, miny1, maxy1
        minx1 = min(minx1, x); maxx1 = max(maxx1, x)
        miny1 = min(miny1, y); maxy1 = max(maxy1, y)

    st = State()
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            code, _ = split_comment(line)
            s = code.strip()
            if not s:
                continue
            up = s.upper()

            if _handle_modal_state_line(st, up):
                continue

            if not st.abs_xy:
                continue

            if MOVE_RE.match(s):
                words = parse_words(code)
                x1 = words.get("X", st.x)
                y1 = words.get("Y", st.y)
                if ("X" in words) or ("Y" in words):
                    upd0(x1, y1)
                    xs, ys = apply_skew_abs(x1, y1, k, y_ref)
                    xs += dx; ys += dy
                    upd1(xs, ys)
                    max_abs_dx = max(max_abs_dx, abs(xs - x1))
                st.x, st.y = x1, y1
                if "E" in words:
                    st.e = words["E"] if st.abs_e else (st.e + words["E"])
                continue

            if ARC_RE.match(s):
                words = parse_words(code)
                cw = (s.split()[0].upper() == "G2")
                pts = linearize_arc_points(st, words, cw=cw, seg_mm=ARC_SEG_MM, max_deg=ARC_MAX_DEG)
                for (xi, yi) in pts:
                    upd0(xi, yi)
                    xs, ys = apply_skew_abs(xi, yi, k, y_ref)
                    xs += dx; ys += dy
                    upd1(xs, ys)
                    max_abs_dx = max(max_abs_dx, abs(xs - xi))
                st.x, st.y = pts[-1]
                if "E" in words:
                    st.e = words["E"] if st.abs_e else (st.e + words["E"])
                if "F" in words:
                    st.f = words["F"]
                continue

    lines: List[str] = []
    lines.append("prusaslicer-skew-fix: analyze-only")
    lines.append(f"  skew_deg: {math.degrees(math.atan(k)):.6f}   k=tan(theta)={k:.8f}")
    lines.append(f"  shear_y_ref: {y_ref:.4f}")
    if recenter:
        minx, maxx, miny, maxy = skew_bounds
        lines.append(f"  recenter: enabled   mode={recenter_mode}   margin={margin:.3f}   eps={EPS:.3f}")
        lines.append(f"  in-bed extruding skewed bounds: X[{minx:.3f},{maxx:.3f}] Y[{miny:.3f},{maxy:.3f}]   shift dx={dx:.3f} dy={dy:.3f}")
    else:
        lines.append("  recenter: disabled")
    if minx0 != float("inf"):
        lines.append(f"  all-move bounds (pre):  X[{minx0:.3f},{maxx0:.3f}] Y[{miny0:.3f},{maxy0:.3f}]")
        lines.append(f"  all-move bounds (post): X[{minx1:.3f},{maxx1:.3f}] Y[{miny1:.3f},{maxy1:.3f}]")
        lines.append(f"  max |ΔX| (all moves): {max_abs_dx:.4f} mm")
    else:
        lines.append("  no XY moves found to analyze.")
    return lines


def _bgcode_split(data: bytes) -> Tuple[bytes, List[bytes], str]:
    """Parse a .bgcode file into its components.

    Returns:
        file_hdr:     the 10-byte file prefix (magic + version + checksum_type).
        other_blocks: raw bytes of every non-GCode block, in original order.
                      These are preserved verbatim on reassembly (thumbnails,
                      slicer/printer/print metadata, etc.).
        gcode_text:   concatenated ASCII G-code from all GCode blocks.

    Raises ValueError for invalid/truncated/unsupported files.
    """
    MAGIC = b"GCDE"
    BLK_GCODE = 1
    BLK_THUMBNAIL = 5   # thumbnail params are 6 bytes; all other blocks use 2
    COMP_NONE = 0
    COMP_DEFLATE = 1
    ENC_RAW = 0         # plain UTF-8; same numeric value as ENC_INI for metadata

    if len(data) < 10:
        raise ValueError(f"Data too short ({len(data)} bytes) to be a bgcode file")
    if data[:4] != MAGIC:
        raise ValueError(f"Not a bgcode file: expected magic {MAGIC!r}, got {data[:4]!r}")

    # File header: magic(4) + version(uint32) + checksum_type(uint16) = 10 bytes
    file_hdr = data[:10]
    pos = 10
    gcode_parts: List[str] = []
    other_raws: List[bytes] = []

    while pos < len(data):
        block_start = pos

        if pos + 8 > len(data):
            raise ValueError(f"Truncated block header at offset {pos}")

        btype, comp = struct.unpack_from("<HH", data, pos)
        uncomp_size, = struct.unpack_from("<I", data, pos + 4)

        if comp == COMP_NONE:
            comp_size = uncomp_size
            hdr_len = 8
        elif comp in (COMP_DEFLATE, 2, 3):  # DEFLATE or either Heatshrink variant
            if pos + 12 > len(data):
                raise ValueError(f"Truncated block header at offset {pos}")
            comp_size, = struct.unpack_from("<I", data, pos + 8)
            hdr_len = 12
        else:
            raise ValueError(f"Unknown compression type {comp} at offset {pos}")

        params_len = 6 if btype == BLK_THUMBNAIL else 2
        params_start = pos + hdr_len
        payload_start = params_start + params_len
        payload_end = payload_start + comp_size

        if payload_end + 4 > len(data):
            raise ValueError(
                f"Truncated block payload at offset {pos}: "
                f"need {payload_end + 4 - len(data)} more bytes"
            )

        # CRC32 covers: block header bytes + params bytes + payload bytes
        stored_crc, = struct.unpack_from("<I", data, payload_end)
        computed_crc = zlib.crc32(data[pos:payload_end]) & 0xFFFFFFFF
        if computed_crc != stored_crc:
            raise ValueError(
                f"CRC32 mismatch in block type {btype} at offset {pos}: "
                f"computed {computed_crc:#010x}, stored {stored_crc:#010x}"
            )

        block_end = payload_end + 4

        if btype == BLK_GCODE:
            enc, = struct.unpack_from("<H", data, params_start)
            payload = data[payload_start:payload_end]
            if comp == COMP_NONE:
                raw_payload = payload
            elif comp == COMP_DEFLATE:
                try:
                    raw_payload = zlib.decompress(payload)
                except zlib.error as e:
                    raise ValueError(
                        f"DEFLATE decompression failed at offset {pos}: {e}"
                    ) from e
            else:
                raise ValueError(
                    f"Unsupported GCode block compression {comp} at offset {pos} "
                    f"(Heatshrink decoding requires a third-party library)"
                )
            if enc != ENC_RAW:
                raise ValueError(
                    f"Unsupported GCode block encoding {enc} at offset {pos} "
                    f"(MeatPack decoding is not supported)"
                )
            gcode_parts.append(raw_payload.decode("utf-8"))
        else:
            # Preserve non-GCode blocks verbatim for reassembly.
            other_raws.append(data[block_start:block_end])

        pos = block_end

    if not gcode_parts:
        raise ValueError("No GCode blocks found in bgcode data")

    return file_hdr, other_raws, "".join(gcode_parts)


def _bgcode_reassemble(file_hdr: bytes, other_blocks: List[bytes], gcode_text: str) -> bytes:
    """Rebuild a .bgcode file with modified G-code text.

    All non-GCode blocks from other_blocks are preserved in their original order.
    A single new GCode block (COMP_NONE, ENC_RAW) is appended last, which is
    the position required by the libbgcode convention (metadata must precede GCode).
    """
    BLK_GCODE = 1
    COMP_NONE = 0
    ENC_RAW = 0

    def _crc(data: bytes, prev: int = 0) -> int:
        return zlib.crc32(data, prev) & 0xFFFFFFFF

    payload = gcode_text.encode("utf-8")
    hdr = struct.pack("<HHI", BLK_GCODE, COMP_NONE, len(payload))
    params = struct.pack("<H", ENC_RAW)
    cksum = _crc(hdr)
    cksum = _crc(params, cksum)
    cksum = _crc(payload, cksum)
    gcode_block = hdr + params + payload + struct.pack("<I", cksum)

    return file_hdr + b"".join(other_blocks) + gcode_block


def _rewrite_bgcode(
    path: str,
    skew_deg: float,
    recenter: bool,
    bed_x_min: float,
    bed_x_max: float,
    bed_y_min: float,
    bed_y_max: float,
    margin: float,
    recenter_mode: str,
    shear_y_ref_mode: str,
    shear_y_ref: float,
    analyze_only: bool,
) -> None:
    """Apply skew correction to a Prusa binary G-code (.bgcode) file in place.

    Strategy:
    1. Split the bgcode into its file header, non-GCode blocks, and G-code text.
    2. Write the G-code text to a temporary plain-text file.
    3. Run the full text rewrite pipeline on that temp file (all existing logic
       is unchanged — bounds calculation, arc linearization, recenter, etc.).
    4. Read back the corrected text, re-encode it as a new GCode block, and
       reassemble the bgcode with the original non-GCode blocks intact.
    5. Atomically replace the original file.

    All non-GCode blocks (thumbnails, slicer/printer/print metadata) are
    preserved bit-for-bit, so the result uploads cleanly to PrusaConnect.
    """
    with open(path, "rb") as f:
        raw = f.read()

    try:
        file_hdr, other_blocks, gcode_text = _bgcode_split(raw)
    except ValueError as exc:
        raise SystemExit(f"prusaslicer-skew-fix: ERROR reading binary G-code: {exc}") from exc

    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, txt_tmp = tempfile.mkstemp(dir=d, suffix=".gcode", text=True)
    txt_removed = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(gcode_text)

        # All existing text-mode logic (bounds, recenter, arc linearization, rewrite)
        # runs unchanged on the temp file — it has no idea it came from a bgcode.
        rewrite(
            txt_tmp,
            skew_deg=skew_deg,
            recenter=recenter,
            bed_x_min=bed_x_min,
            bed_x_max=bed_x_max,
            bed_y_min=bed_y_min,
            bed_y_max=bed_y_max,
            margin=margin,
            recenter_mode=recenter_mode,
            shear_y_ref_mode=shear_y_ref_mode,
            shear_y_ref=shear_y_ref,
            analyze_only=analyze_only,
        )

        if not analyze_only:
            with open(txt_tmp, "r", encoding="utf-8") as f:
                modified_text = f.read()
            os.unlink(txt_tmp)
            txt_removed = True

            new_bgcode = _bgcode_reassemble(file_hdr, other_blocks, modified_text)
            fd2, bin_tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
            try:
                with os.fdopen(fd2, "wb") as f:
                    f.write(new_bgcode)
                os.replace(bin_tmp, path)
            finally:
                if os.path.exists(bin_tmp):
                    try:
                        os.unlink(bin_tmp)
                    except OSError:
                        pass
    finally:
        if not txt_removed and os.path.exists(txt_tmp):
            try:
                os.unlink(txt_tmp)
            except OSError:
                pass


# Rewrite the G-code in-place: arc linearization is ALWAYS enabled.
# HARD-CODED ARC LINEARIZATION PARAMETERS (correctness-first):
#   segment length = 0.20 mm
#   max angle per segment = 5.0 degrees
# Rewrite the G-code in-place: skew and optional recentering.
def rewrite(
    path: str,
    skew_deg: float,recenter: bool,
    bed_x_min: float,
    bed_x_max: float,
    bed_y_min: float,
    bed_y_max: float,
    margin: float,
    recenter_mode: str,shear_y_ref_mode: str,
    shear_y_ref: float,analyze_only: bool,
) -> None:
    """
    Apply XY skew correction to a PrusaSlicer-generated G-code file.

    Accepts both plain-text .gcode and Prusa binary .bgcode files.  Binary
    files are decoded, corrected, and re-encoded with all non-GCode blocks
    (thumbnails, metadata) preserved intact.

    Notes on correctness vs textual similarity:
    - For any XY move (G0/G1) that specifies either X or Y, the transformed endpoint generally depends on BOTH
      coordinates (because x' depends on y). Therefore the rewriter will emit *both* X and Y for such moves,
      even if the original line omitted one of them.
    - Relative XY (G91) output is rejected for skew output (and for recentering), because the transform is
      defined in absolute bed coordinates.
    """
    if _is_bgcode(path):
        _rewrite_bgcode(
            path,
            skew_deg=skew_deg,
            recenter=recenter,
            bed_x_min=bed_x_min,
            bed_x_max=bed_x_max,
            bed_y_min=bed_y_min,
            bed_y_max=bed_y_max,
            margin=margin,
            recenter_mode=recenter_mode,
            shear_y_ref_mode=shear_y_ref_mode,
            shear_y_ref=shear_y_ref,
            analyze_only=analyze_only,
        )
        return
    _assert_text_gcode(path)

    k = math.tan(math.radians(skew_deg))
    st = State()

    # ----- choose shear reference (y_ref) -----
    # Using the in-bed extruding Y-center makes X displacement roughly symmetric, reducing clipping risk.
    if shear_y_ref_mode == "auto":
        _, _, ominy, omaxy = compute_inbed_extruding_bounds_original(
            path,
            bed_x_min,
            bed_x_max,
            bed_y_min,
            bed_y_max,
        )
        y_ref = 0.5 * (ominy + omaxy) if (ominy != 0.0 or omaxy != 0.0) else 0.0
    else:
        y_ref = float(shear_y_ref)

    # ----- optional recenter translation -----
    dx = dy = 0.0
    skew_bounds = (0.0, 0.0, 0.0, 0.0)
    if recenter:
        dx, dy, skew_bounds = compute_translation_for_bounds(
            path,
            k,
            y_ref,
            bed_x_min,
            bed_x_max,
            bed_y_min,
            bed_y_max,
            margin,
            recenter_mode,
            
        )

    # ----- analyze-only path -----
    if analyze_only:
        stats = analyze_gcode(
            path,
            k,
            y_ref,
            bed_x_min,
            bed_x_max,
            bed_y_min,
            bed_y_max,
            recenter,
            margin,
            recenter_mode,
        )
        for line in stats:
            print(line)
        return

    # ----- helpers for rewrite -----
    def _emit_metadata(out) -> None:
        out.write("; prusaslicer-skew-fix: applied XY skew correction\n")
        out.write(f"; prusaslicer-skew-fix: skew_deg={skew_deg}  k=tan(theta)={k:.10f}\n")
        out.write(f"; prusaslicer-skew-fix: shear_y_ref_mode={shear_y_ref_mode}  shear_y_ref={y_ref:.4f}\n")
        out.write(f"; prusaslicer-skew-fix: format XY_DECIMALS={XY_DECIMALS} OTHER_DECIMALS={OTHER_DECIMALS}\n")
        out.write(f"; prusaslicer-skew-fix: linearize_arcs=1  arc_segment_mm={ARC_SEG_MM}  arc_max_deg={ARC_MAX_DEG}\n")
        if recenter:
            minx, maxx, miny, maxy = skew_bounds
            out.write(
                f"; prusaslicer-skew-fix: recenter_to_bed=1  mode={recenter_mode}  margin={margin}  eps={EPS}\n"
            )
            out.write(
                f"; prusaslicer-skew-fix: in-bed extruding skewed bounds X[{minx:.3f},{maxx:.3f}] Y[{miny:.3f},{maxy:.3f}]\n"
            )
            out.write(f"; prusaslicer-skew-fix: applied translation dx={dx:.3f} dy={dy:.3f}\n")
        else:
            out.write("; prusaslicer-skew-fix: recenter_to_bed=0\n")

    def _skew_and_translate_xy(x: float, y: float) -> Tuple[float, float]:
        xs, ys = apply_skew_abs(x, y, k, y_ref)
        return xs + dx, ys + dy

    def _process_arc(line: str, code: str, comment: str, s: str) -> None:
        """Always-linearized arc handling (G2/G3 -> G1).

        Guarantees:
        - No G2/G3 is preserved in output.
        - Feedrate (F) and trailing comment are preserved on the *first* emitted segment only.
        - For relative extrusion (M83), the *printed* per-segment E values sum exactly to the original arc E
          at the configured output precision.
        """
        words = parse_words(code)
        cmd = s.split()[0].upper()
        cw = (cmd == "G2")

        pts = linearize_arc_points(st, words, cw=cw, seg_mm=ARC_SEG_MM, max_deg=ARC_MAX_DEG)
        n = len(pts)

        has_e = "E" in words
        e0 = st.e
        if has_e:
            if st.abs_e:
                e_end = words["E"]
                dE = e_end - e0
            else:
                dE = words["E"]
                e_end = e0 + dE
        else:
            dE = 0.0
            e_end = e0

        has_f = "F" in words
        f_word = words.get("F", None)

        # For relative E (M83), distribute dE across segments in OUTPUT precision and close on the last segment
        # so that the sum of *printed* increments equals dE.
        e_accum_print = 0.0
        per_print = 0.0
        if has_e and (not st.abs_e) and n > 0:
            per_print = round(dE / n, OTHER_DECIMALS)

        for i, (xi, yi) in enumerate(pts, start=1):
            xs, ys = _skew_and_translate_xy(xi, yi)

            l = "G1"
            l += f" X{fmt_axis('X', xs, XY_DECIMALS, OTHER_DECIMALS)}"
            l += f" Y{fmt_axis('Y', ys, XY_DECIMALS, OTHER_DECIMALS)}"

            if has_e:
                if st.abs_e:
                    # Absolute E: emit interpolated absolute E, exact at the end.
                    t = i / n
                    ei = e0 + dE * t
                    if i == n:
                        ei = e_end
                    l += f" E{fmt_axis('E', ei, XY_DECIMALS, OTHER_DECIMALS)}"
                else:
                    # Relative E: emit increments whose printed sum equals dE.
                    if i < n:
                        ei = per_print
                        e_accum_print += ei
                    else:
                        ei = round(dE - e_accum_print, OTHER_DECIMALS)
                    l += f" E{fmt_axis('E', ei, XY_DECIMALS, OTHER_DECIMALS)}"

            # Preserve feedrate on first emitted line only.
            if has_f and f_word is not None and i == 1:
                l += f" F{fmt_axis('F', f_word, XY_DECIMALS, OTHER_DECIMALS)}"

            # Preserve the original comment on the first emitted line only.
            if comment and i == 1:
                l += " " + comment.lstrip()

            out.write(l + "\n")

        # Update state to original (unskewed) arc endpoint and E/F semantics.
        st.x, st.y = pts[-1]
        if has_e:
            st.e = e_end
        if has_f and f_word is not None:
            st.f = f_word

    def _process_move(line: str, code: str, comment: str) -> None:
        words = parse_words(code)
        has_xy = ("X" in words) or ("Y" in words)

        if not has_xy:
            out.write(line + "\n")
        else:
            if not st.abs_xy:
                raise SystemExit("prusaslicer-skew-fix: ERROR: relative XY (G91) is not supported. PrusaSlicer emits absolute XY (G90) toolpaths; relative XY would make skew compensation ambiguous.")

            # Endpoint in original (unskewed) coordinate space.
            x_t = words.get("X", st.x)
            y_t = words.get("Y", st.y)

            xs, ys = _skew_and_translate_xy(x_t, y_t)

            # Replace or append ensures stable formatting and preserves other words ordering.
            new = replace_or_append(code, "X", xs, xy_places=XY_DECIMALS, other_places=OTHER_DECIMALS)
            new = replace_or_append(new, "Y", ys, xy_places=XY_DECIMALS, other_places=OTHER_DECIMALS)
            # Normalize formatting for other axes present on the same motion line.
            for ax in ("Z", "E", "F"):
                if ax in words:
                    new = replace_or_append(new, ax, words[ax], xy_places=XY_DECIMALS, other_places=OTHER_DECIMALS)
            out.write(new.rstrip() + ("" if not comment else " " + comment.lstrip()) + "\n")

            # Update position state to the original (unskewed) endpoint.
            st.x, st.y = x_t, y_t

        # Always update other axes/state from the original line.
        if "E" in words:
            st.e = words["E"] if st.abs_e else (st.e + words["E"])
        if "F" in words:
            st.f = words["F"]
        if "Z" in words:
            st.z = words["Z"] if st.abs_xy else (st.z + words["Z"])

    # ----- rewrite in-place safely -----
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out, open(
            path, "r", encoding="utf-8", errors="replace"
        ) as inp:
            out.write("; --- prusaslicer-skew-fix metadata (inserted by post-processing) ---\n")
            _emit_metadata(out)
            out.write("; --- end prusaslicer-skew-fix metadata ---\n")

            for raw in inp:
                line = raw.rstrip("\n")
                code, comment = split_comment(line)
                s = code.strip()
                up = s.upper()

                if _handle_modal_state_line(st, up):
                    out.write(line + "\n")
                    continue

                # Recenter requires absolute XY.
                if recenter and not st.abs_xy:
                    raise SystemExit("prusaslicer-skew-fix: ERROR: --recenter-to-bed requires absolute XY (G90).")

                if not s:
                    out.write(line + "\n")
                    continue

                if ARC_RE.match(s):
                    _process_arc(line, code, comment, s)
                    continue

                if MOVE_RE.match(s):
                    _process_move(line, code, comment)
                    continue

                out.write(line + "\n")

        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass

# CLI entry point used by PrusaSlicer post-processing.
def main(argv: List[str]) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skew-deg", type=_finite_float, default=None, help="XY skew angle in degrees (e.g. -0.15)")
    ap.add_argument(
        "--skew-from-square",
        type=str,
        default=None,
        metavar="AC,BD,AD",
        help="Derive skew_deg from square diagonals and side length.",
    )
    ap.add_argument(
        "--skew-from-rectangle",
        type=str,
        default=None,
        metavar="AC,BD,AD,AB",
        help="Derive skew_deg from rectangle diagonals and side lengths.",
    )

    ap.add_argument("--shear-y-ref-mode", choices=["auto", "fixed"], default="auto",
                    help="Shear reference for x' = x + (y - y_ref)*tan(theta). "
                         "auto uses the in-bed EXTRUDING Y-center; fixed uses --shear-y-ref.")
    ap.add_argument("--shear-y-ref", type=_finite_float, default=0.0,
                    help="Fixed y_ref for shear (only used when --shear-y-ref-mode=fixed).")

    ap.add_argument("--xy-decimals", type=_non_negative_int, default=3,
                    help="Decimal places to emit for X/Y values (default 3).")
    ap.add_argument("--other-decimals", type=_non_negative_int, default=5,
                    help="Decimal places for E/F/Z/I/J/K/etc. (default 5).")

    ap.add_argument("--analyze-only", action="store_true",
                    help="Analyze the skew/recenter effect and print metrics, but do not rewrite the file.")

    ap.add_argument("--recenter-to-bed", action="store_true",
                    help="Recenter using in-bed EXTRUDING bounds only (ignores purge/wipe outside the bed).")
    ap.add_argument("--recenter-mode", choices=["center", "clamp"], default="center",
                    help="center: place within allowable range mid-point (default). clamp: minimal shift from 0.")
    ap.add_argument("--bed-x-min", type=_finite_float, default=0.0)
    ap.add_argument("--bed-x-max", type=_finite_float, default=250.0)
    ap.add_argument("--bed-y-min", type=_finite_float, default=0.0)
    ap.add_argument("--bed-y-max", type=_finite_float, default=220.0)
    ap.add_argument("--margin", type=_non_negative_float, default=0.0, help="Safety margin (mm) from bed edges.")
    ap.add_argument("gcode", help="Path to generated .gcode (PrusaSlicer supplies this)")
    a = ap.parse_args(argv)
    src_count = int(a.skew_deg is not None) + int(a.skew_from_square is not None) + int(a.skew_from_rectangle is not None)
    if src_count != 1:
        ap.error("Specify exactly one of --skew-deg, --skew-from-square, or --skew-from-rectangle.")
    try:
        if a.skew_deg is not None:
            skew_deg = a.skew_deg
        elif a.skew_from_square is not None:
            skew_deg = skew_deg_from_square(a.skew_from_square)
        else:
            skew_deg = skew_deg_from_rectangle(a.skew_from_rectangle)
    except ValueError as exc:
        ap.error(str(exc))
    if a.bed_x_min > a.bed_x_max:
        ap.error("--bed-x-min must be <= --bed-x-max.")
    if a.bed_y_min > a.bed_y_max:
        ap.error("--bed-y-min must be <= --bed-y-max.")

    # Apply formatting settings from CLI. These are used throughout rewrite() via globals.
    global XY_DECIMALS, OTHER_DECIMALS
    XY_DECIMALS = a.xy_decimals
    OTHER_DECIMALS = a.other_decimals

    path = a.gcode

    rewrite(
        path,
        skew_deg=skew_deg,
        shear_y_ref_mode=a.shear_y_ref_mode,
        shear_y_ref=a.shear_y_ref,
        recenter=a.recenter_to_bed,
        bed_x_min=a.bed_x_min,
        bed_x_max=a.bed_x_max,
        bed_y_min=a.bed_y_min,
        bed_y_max=a.bed_y_max,
        margin=a.margin,
        recenter_mode=a.recenter_mode,
        analyze_only=a.analyze_only,
    )

if __name__ == "__main__":
    main(sys.argv[1:])
