#!/usr/bin/env python3
"""
prusaslicer-skew-fix

PrusaSlicer post-processing hook that applies XY skew correction to generated **text** G-code.

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

Binary G-code guard:
- If the input file is Prusa Binary G-code (.bgcode; magic 'GCDE') or appears binary,
  the script aborts to avoid corrupting it.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

MOVE_RE = re.compile(r"^(G0|G1)\b", re.IGNORECASE)
ARC_RE  = re.compile(r"^(G2|G3)\b", re.IGNORECASE)
AXIS_RE = re.compile(r"([XYZEFRIJK])\s*(-?\d+(?:\.\d*)?|-?\.\d+)", re.IGNORECASE)

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
    pat = re.compile(rf"(?i)\b{axis}\s*(-?\d+(?:\.\d*)?|-?\.\d+)\b")
    return pat.sub(tok, code, 1) if pat.search(code) else (code + " " + tok)

# Abort if the input file is binary (.bgcode / NUL bytes) to avoid corruption.
def _assert_text_gcode(path: str) -> None:
    with open(path, "rb") as f:
        head = f.read(512)
    if head.startswith(b"GCDE") or b"GCDE" in head[:64]:
        raise SystemExit(
            "prusaslicer-skew-fix: ERROR: Binary G-code detected (magic 'GCDE').\n"
            "This script only supports text .gcode.\n"
            "Fix: Disable 'Binary G-code' output in PrusaSlicer, then re-slice."
        )
    if b"\x00" in head:
        raise SystemExit(
            "prusaslicer-skew-fix: ERROR: File appears to be binary (NUL bytes detected).\n"
            "This script only supports text .gcode.\n"
            "Fix: Disable 'Binary G-code' output in PrusaSlicer, then re-slice."
        )

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


# Compute original (unskewed) in-bed extruding bounds (endpoints only for G0/G1, optional linearized arc points).
def compute_inbed_extruding_bounds_original(path: str, linearize: bool,
                                           arc_seg_mm: float, arc_max_deg: float,
                                           bed_x_min: float, bed_x_max: float,
                                           bed_y_min: float, bed_y_max: float) -> Tuple[float,float,float,float]:
    st = State()
    minx = float("inf"); maxx = float("-inf")
    miny = float("inf"); maxy = float("-inf")

    def upd(x: float, y: float):
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

            if up.startswith("G90.1"):
                st.ij_relative = False; continue
            if up.startswith("G91.1"):
                st.ij_relative = True; continue
            if up.startswith("G90"):
                st.abs_xy = True; continue
            if up.startswith("G91"):
                st.abs_xy = False; continue
            if up.startswith("M82"):
                st.abs_e = True; continue
            if up.startswith("M83"):
                st.abs_e = False; continue

            if ARC_RE.match(s):
                words = parse_words(code)
                extruding = _is_extruding_move(st, words)

                if extruding:
                    if linearize:
                        cw = (s.split()[0].upper() == "G2")
                        pts = linearize_arc_points(st, words, cw=cw, seg_mm=arc_seg_mm, max_deg=arc_max_deg)
                        for (xi, yi) in pts:
                            if _in_bed(xi, yi, bed_x_min, bed_x_max, bed_y_min, bed_y_max):
                                upd(xi, yi)
                    else:
                        x1, y1 = _arc_end_abs(st, words)
                        if _in_bed(x1, y1, bed_x_min, bed_x_max, bed_y_min, bed_y_max):
                            upd(x1, y1)

                x1, y1 = _arc_end_abs(st, words)
                st.x, st.y = x1, y1
                if "E" in words:
                    st.e = words["E"] if st.abs_e else (st.e + words["E"])
                continue

            if MOVE_RE.match(s):
                words = parse_words(code)
                extruding = _is_extruding_move(st, words)
                x1 = words.get("X", st.x)
                y1 = words.get("Y", st.y)

                if extruding and _in_bed(x1, y1, bed_x_min, bed_x_max, bed_y_min, bed_y_max):
                    upd(x1, y1)

                st.x, st.y = x1, y1
                if "E" in words:
                    st.e = words["E"] if st.abs_e else (st.e + words["E"])
                continue

            if "E" in up:
                words = parse_words(code)
                if "E" in words:
                    st.e = words["E"] if st.abs_e else (st.e + words["E"])

    if minx == float("inf"):
        return (0.0, 0.0, 0.0, 0.0)
    return (minx, maxx, miny, maxy)

# Compute dx/dy so skewed extruding in-bed geometry stays within the bed.
def compute_translation_for_bounds(path: str, k: float, y_ref: float, linearize: bool,
                                  arc_seg_mm: float, arc_max_deg: float,
                                  bed_x_min: float, bed_x_max: float,
                                  bed_y_min: float, bed_y_max: float,
                                  margin: float,
                                  recenter_mode: str,
                                  eps: float) -> Tuple[float, float, Tuple[float,float,float,float]]:
    st = State()
    minx = float("inf"); maxx = float("-inf")
    miny = float("inf"); maxy = float("-inf")

    def upd(xs: float, ys: float):
        nonlocal minx, maxx, miny, maxy
        minx = min(minx, xs); maxx = max(maxx, xs)
        miny = min(miny, ys); maxy = max(maxy, ys)

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            code, _ = split_comment(line)
            s = code.strip()
            if not s:
                continue
            up = s.upper()

            if up.startswith("G90.1"):
                st.ij_relative = False; continue
            if up.startswith("G91.1"):
                st.ij_relative = True; continue
            if up.startswith("G90"):
                st.abs_xy = True; continue
            if up.startswith("G91"):
                st.abs_xy = False; continue
            if up.startswith("M82"):
                st.abs_e = True; continue
            if up.startswith("M83"):
                st.abs_e = False; continue

            if not st.abs_xy:
                raise SystemExit("prusaslicer-skew-fix: ERROR: --recenter-to-bed requires absolute XY (G90).")

            if ARC_RE.match(s):
                words = parse_words(code)
                extruding = _is_extruding_move(st, words)

                if extruding:
                    if linearize:
                        cw = (s.split()[0].upper() == "G2")
                        pts = linearize_arc_points(st, words, cw=cw, seg_mm=arc_seg_mm, max_deg=arc_max_deg)
                        for (xi, yi) in pts:
                            if _in_bed(xi, yi, bed_x_min, bed_x_max, bed_y_min, bed_y_max):
                                xs, ys = apply_skew_abs(xi, yi, k, y_ref)
                                upd(xs, ys)
                    else:
                        x1, y1 = _arc_end_abs(st, words)
                        if _in_bed(x1, y1, bed_x_min, bed_x_max, bed_y_min, bed_y_max):
                            xs, ys = apply_skew_abs(x1, y1, k, y_ref)
                            upd(xs, ys)

                x1, y1 = _arc_end_abs(st, words)
                st.x, st.y = x1, y1
                if "E" in words:
                    st.e = words["E"] if st.abs_e else (st.e + words["E"])
                continue

            if MOVE_RE.match(s):
                words = parse_words(code)
                extruding = _is_extruding_move(st, words)
                x1 = words.get("X", st.x)
                y1 = words.get("Y", st.y)

                if extruding and _in_bed(x1, y1, bed_x_min, bed_x_max, bed_y_min, bed_y_max):
                    xs, ys = apply_skew_abs(x1, y1, k, y_ref)
                    upd(xs, ys)

                st.x, st.y = x1, y1
                if "E" in words:
                    st.e = words["E"] if st.abs_e else (st.e + words["E"])
                continue

            if "E" in up:
                words = parse_words(code)
                if "E" in words:
                    st.e = words["E"] if st.abs_e else (st.e + words["E"])

    if minx == float("inf"):
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


def analyze_gcode(path: str, k: float, y_ref: float,
                 linearize: bool, arc_seg_mm: float, arc_max_deg: float,
                 bed_x_min: float, bed_x_max: float, bed_y_min: float, bed_y_max: float,
                 recenter: bool, margin: float, recenter_mode: str, eps: float) -> List[str]:
    """Analyze the effect of skew (and optional recenter) without rewriting the file."""
    _assert_text_gcode(path)

    dx = dy = 0.0
    skew_bounds = (0.0, 0.0, 0.0, 0.0)
    if recenter:
        dx, dy, skew_bounds = compute_translation_for_bounds(
            path, k, y_ref, linearize, arc_seg_mm, arc_max_deg,
            bed_x_min, bed_x_max, bed_y_min, bed_y_max, margin,
            recenter_mode, eps
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

            if up.startswith("G90.1"):
                st.ij_relative = False; continue
            if up.startswith("G91.1"):
                st.ij_relative = True; continue
            if up.startswith("G90"):
                st.abs_xy = True; continue
            if up.startswith("G91"):
                st.abs_xy = False; continue
            if up.startswith("M82"):
                st.abs_e = True; continue
            if up.startswith("M83"):
                st.abs_e = False; continue

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
                if linearize:
                    cw = (s.split()[0].upper() == "G2")
                    pts = linearize_arc_points(st, words, cw=cw, seg_mm=arc_seg_mm, max_deg=arc_max_deg)
                    for (xi, yi) in pts:
                        upd0(xi, yi)
                        xs, ys = apply_skew_abs(xi, yi, k, y_ref)
                        xs += dx; ys += dy
                        upd1(xs, ys)
                        max_abs_dx = max(max_abs_dx, abs(xs - xi))
                    st.x, st.y = pts[-1]
                else:
                    x1, y1 = _arc_end_abs(st, words)
                    upd0(x1, y1)
                    xs, ys = apply_skew_abs(x1, y1, k, y_ref)
                    xs += dx; ys += dy
                    upd1(xs, ys)
                    max_abs_dx = max(max_abs_dx, abs(xs - x1))
                    st.x, st.y = x1, y1

                if "E" in words:
                    st.e = words["E"] if st.abs_e else (st.e + words["E"])
                continue

    lines: List[str] = []
    lines.append("prusaslicer-skew-fix: analyze-only")
    lines.append(f"  skew_deg: {math.degrees(math.atan(k)):.6f}   k=tan(theta)={k:.8f}")
    lines.append(f"  shear_y_ref: {y_ref:.4f}")
    if recenter:
        minx, maxx, miny, maxy = skew_bounds
        lines.append(f"  recenter: enabled   mode={recenter_mode}   margin={margin:.3f}   eps={eps:.3f}")
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


# Rewrite the G-code in-place: optional arc linearization, skew, and optional recentering.
def rewrite(path: str, skew_deg: float,
            linearize: bool, arc_seg_mm: float, arc_max_deg: float,
            recenter: bool, bed_x_min: float, bed_x_max: float,
            bed_y_min: float, bed_y_max: float, margin: float,
            recenter_mode: str, eps: float,
            shear_y_ref_mode: str, shear_y_ref: float,
            xy_decimals: int, other_decimals: int,
            analyze_only: bool) -> None:
    _assert_text_gcode(path)
    k = math.tan(math.radians(skew_deg))
    st = State()

    # Choose shear reference (y_ref). Using the model's Y center makes X displacement
    # roughly symmetric, reducing the chance of clipping one bed edge.
    if shear_y_ref_mode == "auto":
        _, _, ominy, omaxy = compute_inbed_extruding_bounds_original(
            path, linearize, arc_seg_mm, arc_max_deg,
            bed_x_min, bed_x_max, bed_y_min, bed_y_max
        )
        y_ref = 0.5 * (ominy + omaxy) if (ominy != 0.0 or omaxy != 0.0) else 0.0
    else:
        y_ref = float(shear_y_ref)

    dx = dy = 0.0
    skew_bounds = (0.0, 0.0, 0.0, 0.0)
    if recenter:
        dx, dy, skew_bounds = compute_translation_for_bounds(
            path, k, y_ref, linearize, arc_seg_mm, arc_max_deg,
            bed_x_min, bed_x_max, bed_y_min, bed_y_max, margin,
            recenter_mode, eps
        )

    
    if analyze_only:
        stats = analyze_gcode(path, k, y_ref,
                             linearize, arc_seg_mm, arc_max_deg,
                             bed_x_min, bed_x_max, bed_y_min, bed_y_max,
                             recenter, margin, recenter_mode, eps)
        for line in stats:
            print(line)
        return

    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out, \
             open(path, "r", encoding="utf-8", errors="replace") as inp:

            # --- prusaslicer-skew-fix metadata (inserted by post-processing) ---
            out.write("; prusaslicer-skew-fix: applied XY skew correction\n")
            out.write(f"; prusaslicer-skew-fix: skew_deg={skew_deg}  k=tan(theta)={k:.10f}\n")
            out.write(f"; prusaslicer-skew-fix: shear_y_ref_mode={shear_y_ref_mode}  shear_y_ref={y_ref:.4f}\n")
            out.write(f"; prusaslicer-skew-fix: format xy_decimals={xy_decimals} other_decimals={other_decimals}\n")
            if linearize:
                out.write(f"; prusaslicer-skew-fix: linearize_arcs=1  arc_segment_mm={arc_seg_mm}  arc_max_deg={arc_max_deg}\n")
            else:
                out.write("; prusaslicer-skew-fix: linearize_arcs=0\n")
            if recenter:
                minx, maxx, miny, maxy = skew_bounds
                out.write(f"; prusaslicer-skew-fix: recenter_to_bed=1  mode={recenter_mode}  margin={margin}  eps={eps}\n")
                out.write(f"; prusaslicer-skew-fix: in-bed extruding skewed bounds X[{minx:.3f},{maxx:.3f}] Y[{miny:.3f},{maxy:.3f}]\n")
                out.write(f"; prusaslicer-skew-fix: applied translation dx={dx:.3f} dy={dy:.3f}\n")
            else:
                out.write("; prusaslicer-skew-fix: recenter_to_bed=0\n")
            out.write("; --- end prusaslicer-skew-fix metadata ---\n")

            for raw in inp:
                line = raw.rstrip("\n")
                code, comment = split_comment(line)
                s = code.strip()
                up = s.upper()

                if up.startswith("G90.1"):
                    st.ij_relative = False; out.write(line + "\n"); continue
                if up.startswith("G91.1"):
                    st.ij_relative = True; out.write(line + "\n"); continue
                if up.startswith("G90"):
                    st.abs_xy = True; out.write(line + "\n"); continue
                if up.startswith("G91"):
                    st.abs_xy = False; out.write(line + "\n"); continue
                if up.startswith("M82"):
                    st.abs_e = True; out.write(line + "\n"); continue
                if up.startswith("M83"):
                    st.abs_e = False; out.write(line + "\n"); continue

                if recenter and not st.abs_xy:
                    raise SystemExit("prusaslicer-skew-fix: ERROR: --recenter-to-bed requires absolute XY (G90).")

                if ARC_RE.match(s):
                    words = parse_words(code)
                    cmd = s.split()[0].upper()
                    cw = (cmd == "G2")

                    if linearize:
                        pts = linearize_arc_points(st, words, cw=cw, seg_mm=arc_seg_mm, max_deg=arc_max_deg)

                        has_e = "E" in words
                        e0 = st.e
                        if has_e:
                            if st.abs_e:
                                e_end = words["E"]; dE = e_end - e0
                            else:
                                dE = words["E"]; e_end = e0 + dE
                        else:
                            dE = 0.0; e_end = e0

                        has_f = "F" in words
                        f_word = words.get("F", None)

                        for i, (xi, yi) in enumerate(pts, start=1):
                            xs, ys = apply_skew_abs(xi, yi, k, y_ref)
                            xs += dx; ys += dy
                            l = "G1"
                            l += f" X{fmt_axis('X', xs, xy_decimals, other_decimals)} Y{fmt_axis('Y', ys, xy_decimals, other_decimals)}"
                            if has_e:
                                if st.abs_e:
                                    t = i / len(pts)
                                    ei = e0 + dE * t
                                    if i == len(pts): ei = e_end
                                    l += f" E{fmt_axis('E', ei, xy_decimals, other_decimals)}"
                                else:
                                    l += f" E{fmt_axis('E', (dE / len(pts)), xy_decimals, other_decimals)}"
                            if has_f and f_word is not None and i == 1:
                                l += f" F{fmt_axis('F', f_word, xy_decimals, other_decimals)}"
                            if comment and i == 1:
                                l += " " + comment.lstrip()
                            out.write(l + "\n")

                        st.x, st.y = pts[-1]
                        if has_e: st.e = e_end
                        if has_f and f_word is not None: st.f = f_word
                    else:
                        x1, y1 = _arc_end_abs(st, words)
                        st.x, st.y = x1, y1
                        if "E" in words:
                            st.e = words["E"] if st.abs_e else (st.e + words["E"])
                        if "F" in words:
                            st.f = words["F"]
                        out.write(line + "\n")
                    continue

                if MOVE_RE.match(s):
                    words = parse_words(code)
                    has_x = "X" in words
                    has_y = "Y" in words
                    if has_x or has_y:
                        if not st.abs_xy:
                            raise SystemExit("prusaslicer-skew-fix: ERROR: relative XY (G91) not supported for skew output.")
                        x_t = words.get("X", st.x)
                        y_t = words.get("Y", st.y)
                        xs, ys = apply_skew_abs(x_t, y_t, k, y_ref)
                        xs += dx; ys += dy
                        new = code
                        if has_x: new = replace_or_append(new, "X", xs, xy_places=xy_decimals, other_places=other_decimals)
                        if has_y: new = replace_or_append(new, "Y", ys, xy_places=xy_decimals, other_places=other_decimals)
                        out.write(new.rstrip() + ("" if not comment else " " + comment.lstrip()) + "\n")
                    else:
                        out.write(line + "\n")

                    if has_x: st.x = words["X"]
                    if has_y: st.y = words["Y"]
                    if "E" in words:
                        st.e = words["E"] if st.abs_e else (st.e + words["E"])
                    if "F" in words:
                        st.f = words["F"]
                    if "Z" in words:
                        st.z = words["Z"] if st.abs_xy else (st.z + words["Z"])
                    continue

                out.write(line + "\n")

        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try: os.unlink(tmp)
            except OSError: pass

# CLI entry point used by PrusaSlicer post-processing.
def main(argv: List[str]) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skew-deg", type=float, required=True, help="XY skew angle in degrees (e.g. -0.15)")

    ap.add_argument("--shear-y-ref-mode", choices=["auto", "fixed"], default="auto",
                    help="Shear reference for x' = x + (y - y_ref)*tan(theta). "
                         "auto uses the in-bed EXTRUDING Y-center; fixed uses --shear-y-ref.")
    ap.add_argument("--shear-y-ref", type=float, default=0.0,
                    help="Fixed y_ref for shear (only used when --shear-y-ref-mode=fixed).")

    ap.add_argument("--xy-decimals", type=int, default=3,
                    help="Decimal places to emit for X/Y values (default 3).")
    ap.add_argument("--other-decimals", type=int, default=5,
                    help="Decimal places for E/F/Z/I/J/K/etc. (default 5).")

    ap.add_argument("--analyze-only", action="store_true",
                    help="Analyze the skew/recenter effect and print metrics, but do not rewrite the file.")

    ap.add_argument("--linearize-arcs", action="store_true",
                    help="Convert G2/G3 arcs to G1 segments before applying skew (recommended if arcs exist)")
    ap.add_argument("--arc-segment-mm", type=float, default=0.20,
                    help="Target max chord length for arc linearization (mm).")
    ap.add_argument("--arc-max-deg", type=float, default=5.0,
                    help="Max angle per segment for arc linearization (degrees).")

    ap.add_argument("--recenter-to-bed", action="store_true",
                    help="Recenter using in-bed EXTRUDING bounds only (ignores purge/wipe outside the bed).")
    ap.add_argument("--recenter-mode", choices=["center", "clamp"], default="center",
                    help="center: place within allowable range mid-point (default). clamp: minimal shift from 0.")
    ap.add_argument("--bed-x-min", type=float, default=0.0)
    ap.add_argument("--bed-x-max", type=float, default=250.0)
    ap.add_argument("--bed-y-min", type=float, default=0.0)
    ap.add_argument("--bed-y-max", type=float, default=220.0)
    ap.add_argument("--margin", type=float, default=0.0, help="Safety margin (mm) from bed edges.")
    ap.add_argument("--eps", type=float, default=0.01, help="Bounds tolerance (mm) to avoid tiny floating-point failures.")

    ap.add_argument("gcode", help="Path to generated .gcode (PrusaSlicer supplies this)")
    a = ap.parse_args(argv)

    rewrite(a.gcode, a.skew_deg,
            a.linearize_arcs, a.arc_segment_mm, a.arc_max_deg,
            a.recenter_to_bed, a.bed_x_min, a.bed_x_max, a.bed_y_min, a.bed_y_max, a.margin,
            a.recenter_mode, a.eps,
            a.shear_y_ref_mode, a.shear_y_ref,
            a.xy_decimals, a.other_decimals,
            a.analyze_only)

if __name__ == "__main__":
    main(sys.argv[1:])
