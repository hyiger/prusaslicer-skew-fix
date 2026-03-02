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
import sys
from typing import List, Optional, Tuple, Union

import gcode_lib

EPS = gcode_lib.EPS


# ---------------------------------------------------------------------------
# Application-specific helpers
# ---------------------------------------------------------------------------

def _in_bed(x: float, y: float, xmin: float, xmax: float, ymin: float, ymax: float) -> bool:
    """Return True if (x, y) lies within the printable bed rectangle."""
    return (xmin <= x <= xmax) and (ymin <= y <= ymax)


def _is_extruding(st: gcode_lib.ModalState, words: dict) -> bool:
    """Return True if a move deposits plastic based on current extrusion mode/state."""
    if "E" not in words:
        return False
    e_word = words["E"]
    if st.abs_e:
        return e_word > st.e
    return e_word > 0.0


def _choose_translation(lo: float, hi: float, mode: str) -> float:
    """Choose dx/dy using 'center' or minimal-shift 'clamp' strategy."""
    if mode == "center":
        return 0.5 * (lo + hi)
    if lo <= 0.0 <= hi:
        return 0.0
    return lo if abs(lo) < abs(hi) else hi


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# CLI validation helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# In-bed extruding bounds (application-specific)
# ---------------------------------------------------------------------------

def _scan_inbed_bounds(
    lines: List[gcode_lib.GCodeLine],
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
    minx = float("inf"); maxx = float("-inf")
    miny = float("inf"); maxy = float("-inf")

    def upd(x: float, y: float) -> None:
        nonlocal minx, maxx, miny, maxy
        minx = min(minx, x); maxx = max(maxx, x)
        miny = min(miny, y); maxy = max(maxy, y)

    for line, st in gcode_lib.iter_with_state(lines):
        # Skip blank and comment-only lines
        if not line.command and not line.words:
            continue

        # Modal-only lines (G90, G91, etc.) are handled by iter_with_state;
        # we only need the require_abs_xy check for non-modal lines.
        if line.command in ("G90", "G91", "M82", "M83", "G90.1", "G91.1", "G92"):
            continue

        if require_abs_xy and not st.abs_xy:
            raise SystemExit("prusaslicer-skew-fix: ERROR: --recenter-to-bed requires absolute XY (G90).")

        if line.is_arc:
            cw = (line.command.upper() == "G2")
            pts = gcode_lib.linearize_arc_points(st, line.words, cw)
            if _is_extruding(st, line.words):
                for xi, yi in pts:
                    if _in_bed(xi, yi, bed_x_min, bed_x_max, bed_y_min, bed_y_max):
                        xb, yb = move_point_fn(xi, yi)
                        upd(xb, yb)
            continue

        if line.is_move and ("X" in line.words or "Y" in line.words):
            if st.abs_xy:
                x1 = line.words.get("X", st.x)
                y1 = line.words.get("Y", st.y)
            else:
                x1 = st.x + line.words.get("X", 0.0)
                y1 = st.y + line.words.get("Y", 0.0)
            if _is_extruding(st, line.words) and _in_bed(x1, y1, bed_x_min, bed_x_max, bed_y_min, bed_y_max):
                xb, yb = move_point_fn(x1, y1)
                upd(xb, yb)
            continue

    if minx == float("inf"):
        return (0.0, 0.0, 0.0, 0.0, False)
    return (minx, maxx, miny, maxy, True)


def compute_inbed_extruding_bounds_original(
    path_or_lines: Union[str, List[gcode_lib.GCodeLine]],
    bed_x_min: float,
    bed_x_max: float,
    bed_y_min: float,
    bed_y_max: float,
) -> Tuple[float, float, float, float]:
    """Compute original (unskewed) in-bed extruding bounds."""
    if isinstance(path_or_lines, str):
        lines = gcode_lib.load(path_or_lines).lines
    else:
        lines = path_or_lines
    minx, maxx, miny, maxy, _ = _scan_inbed_bounds(
        lines,
        bed_x_min=bed_x_min,
        bed_x_max=bed_x_max,
        bed_y_min=bed_y_min,
        bed_y_max=bed_y_max,
        require_abs_xy=False,
        move_point_fn=lambda x, y: (x, y),
    )
    return minx, maxx, miny, maxy


def compute_translation_for_bounds(
    path_or_lines: Union[str, List[gcode_lib.GCodeLine]],
    k: float,
    y_ref: float,
    bed_x_min: float,
    bed_x_max: float,
    bed_y_min: float,
    bed_y_max: float,
    margin: float,
    recenter_mode: str,
) -> Tuple[float, float, Tuple[float, float, float, float]]:
    """Compute dx/dy so skewed extruding in-bed geometry stays within the bed."""
    if isinstance(path_or_lines, str):
        lines = gcode_lib.load(path_or_lines).lines
    else:
        lines = path_or_lines

    def _skew_point(x: float, y: float) -> Tuple[float, float]:
        return (x + (y - y_ref) * k, y)

    minx, maxx, miny, maxy, has_points = _scan_inbed_bounds(
        lines,
        bed_x_min=bed_x_min,
        bed_x_max=bed_x_max,
        bed_y_min=bed_y_min,
        bed_y_max=bed_y_max,
        require_abs_xy=True,
        move_point_fn=_skew_point,
    )

    if not has_points:
        return 0.0, 0.0, (0.0, 0.0, 0.0, 0.0)

    dx_lo = (bed_x_min + margin) - minx
    dx_hi = (bed_x_max - margin) - maxx
    dy_lo = (bed_y_min + margin) - miny
    dy_hi = (bed_y_max - margin) - maxy

    if (dx_lo - dx_hi) > EPS or (dy_lo - dy_hi) > EPS:
        raise SystemExit(
            "prusaslicer-skew-fix: ERROR: Model geometry cannot fit within bed after skew.\n"
            f"Skewed in-bed extruding bounds: X[{minx:.3f}, {maxx:.3f}] Y[{miny:.3f}, {maxy:.3f}]\n"
            f"Bed bounds: X[{bed_x_min:.3f}, {bed_x_max:.3f}] "
            f"Y[{bed_y_min:.3f}, {bed_y_max:.3f}] (margin {margin:.3f})"
        )

    dx = _choose_translation(dx_lo, dx_hi, recenter_mode)
    dy = _choose_translation(dy_lo, dy_hi, recenter_mode)
    return dx, dy, (minx, maxx, miny, maxy)


# ---------------------------------------------------------------------------
# Analysis (dry-run)
# ---------------------------------------------------------------------------

def analyze_gcode(
    path_or_lines: Union[str, List[gcode_lib.GCodeLine]],
    k: float,
    y_ref: float,
    bed_x_min: float,
    bed_x_max: float,
    bed_y_min: float,
    bed_y_max: float,
    recenter: bool,
    margin: float,
    recenter_mode: str,
) -> List[str]:
    """Analyze the effect of skew (and optional recenter) without rewriting the file."""
    if isinstance(path_or_lines, str):
        lines = gcode_lib.load(path_or_lines).lines
    else:
        lines = path_or_lines

    dx = dy = 0.0
    skew_bounds = (0.0, 0.0, 0.0, 0.0)
    if recenter:
        dx, dy, skew_bounds = compute_translation_for_bounds(
            lines, k, y_ref,
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

    for line, st in gcode_lib.iter_with_state(lines):
        if not st.abs_xy:
            continue

        if line.is_move:
            x1 = line.words.get("X", st.x)
            y1 = line.words.get("Y", st.y)
            if "X" in line.words or "Y" in line.words:
                upd0(x1, y1)
                xs = x1 + (y1 - y_ref) * k + dx
                ys = y1 + dy
                upd1(xs, ys)
                max_abs_dx = max(max_abs_dx, abs(xs - x1))
            continue

        if line.is_arc:
            cw = (line.command.upper() == "G2")
            pts = gcode_lib.linearize_arc_points(st, line.words, cw)
            for xi, yi in pts:
                upd0(xi, yi)
                xs = xi + (yi - y_ref) * k + dx
                ys = yi + dy
                upd1(xs, ys)
                max_abs_dx = max(max_abs_dx, abs(xs - xi))
            continue

    report: List[str] = []
    report.append("prusaslicer-skew-fix: analyze-only")
    report.append(f"  skew_deg: {math.degrees(math.atan(k)):.6f}   k=tan(theta)={k:.8f}")
    report.append(f"  shear_y_ref: {y_ref:.4f}")
    if recenter:
        sminx, smaxx, sminy, smaxy = skew_bounds
        report.append(f"  recenter: enabled   mode={recenter_mode}   margin={margin:.3f}   eps={EPS:.3f}")
        report.append(f"  in-bed extruding skewed bounds: X[{sminx:.3f},{smaxx:.3f}] Y[{sminy:.3f},{smaxy:.3f}]   shift dx={dx:.3f} dy={dy:.3f}")
    else:
        report.append("  recenter: disabled")
    if minx0 != float("inf"):
        report.append(f"  all-move bounds (pre):  X[{minx0:.3f},{maxx0:.3f}] Y[{miny0:.3f},{maxy0:.3f}]")
        report.append(f"  all-move bounds (post): X[{minx1:.3f},{maxx1:.3f}] Y[{miny1:.3f},{maxy1:.3f}]")
        report.append(f"  max |ΔX| (all moves): {max_abs_dx:.4f} mm")
    else:
        report.append("  no XY moves found to analyze.")
    return report


# ---------------------------------------------------------------------------
# Metadata comment builder
# ---------------------------------------------------------------------------

def _build_metadata_lines(
    skew_deg: float,
    k: float,
    y_ref: float,
    shear_y_ref_mode: str,
    xy_decimals: int,
    other_decimals: int,
    recenter: bool,
    recenter_mode: str,
    margin: float,
    skew_bounds: Tuple[float, float, float, float],
    dx: float,
    dy: float,
) -> List[gcode_lib.GCodeLine]:
    """Build metadata comment lines to prepend to the output."""
    seg_mm = gcode_lib.DEFAULT_ARC_SEG_MM
    max_deg = gcode_lib.DEFAULT_ARC_MAX_DEG

    raw_lines = [
        "; --- prusaslicer-skew-fix metadata (inserted by post-processing) ---",
        "; prusaslicer-skew-fix: applied XY skew correction",
        f"; prusaslicer-skew-fix: skew_deg={skew_deg}  k=tan(theta)={k:.10f}",
        f"; prusaslicer-skew-fix: shear_y_ref_mode={shear_y_ref_mode}  shear_y_ref={y_ref:.4f}",
        f"; prusaslicer-skew-fix: format XY_DECIMALS={xy_decimals} OTHER_DECIMALS={other_decimals}",
        f"; prusaslicer-skew-fix: linearize_arcs=1  arc_segment_mm={seg_mm}  arc_max_deg={max_deg}",
    ]
    if recenter:
        minx, maxx, miny, maxy = skew_bounds
        raw_lines.append(
            f"; prusaslicer-skew-fix: recenter_to_bed=1  mode={recenter_mode}  margin={margin}  eps={EPS}"
        )
        raw_lines.append(
            f"; prusaslicer-skew-fix: in-bed extruding skewed bounds X[{minx:.3f},{maxx:.3f}] Y[{miny:.3f},{maxy:.3f}]"
        )
        raw_lines.append(f"; prusaslicer-skew-fix: applied translation dx={dx:.3f} dy={dy:.3f}")
    else:
        raw_lines.append("; prusaslicer-skew-fix: recenter_to_bed=0")
    raw_lines.append("; --- end prusaslicer-skew-fix metadata ---")

    return [gcode_lib.parse_line(rl) for rl in raw_lines]


# ---------------------------------------------------------------------------
# Main rewrite engine
# ---------------------------------------------------------------------------

def rewrite(
    path: str,
    skew_deg: float,
    recenter: bool,
    bed_x_min: Optional[float],
    bed_x_max: Optional[float],
    bed_y_min: Optional[float],
    bed_y_max: Optional[float],
    margin: float,
    recenter_mode: str,
    shear_y_ref_mode: str,
    shear_y_ref: float,
    analyze_only: bool,
    xy_decimals: int = gcode_lib.DEFAULT_XY_DECIMALS,
    other_decimals: int = gcode_lib.DEFAULT_OTHER_DECIMALS,
) -> None:
    """Apply XY skew correction to a PrusaSlicer-generated G-code file.

    Accepts both plain-text .gcode and Prusa binary .bgcode files.  Binary
    files are decoded, corrected, and re-encoded with all non-GCode blocks
    (thumbnails, metadata) preserved intact.

    Bed bounds (bed_x_min/max, bed_y_min/max) are auto-detected from the
    G-code ``M862.3 P`` printer-model check when not explicitly provided.
    Falls back to 250 x 220 mm (Core ONE) if detection fails.
    """
    # 1. Load file (auto-detects text/bgcode)
    try:
        gf = gcode_lib.load(path)
    except ValueError as exc:
        raise SystemExit(f"prusaslicer-skew-fix: ERROR: {exc}") from exc

    # 2. Auto-detect bed bounds from printer model if not explicitly set
    if bed_x_min is None or bed_x_max is None or bed_y_min is None or bed_y_max is None:
        vol = gcode_lib.detect_print_volume(gf.lines)
        if vol is not None:
            if bed_x_min is None:
                bed_x_min = 0.0
            if bed_x_max is None:
                bed_x_max = vol["bed_x"]
            if bed_y_min is None:
                bed_y_min = 0.0
            if bed_y_max is None:
                bed_y_max = vol["bed_y"]
        else:
            # Fallback defaults (Core ONE)
            if bed_x_min is None:
                bed_x_min = 0.0
            if bed_x_max is None:
                bed_x_max = 250.0
            if bed_y_min is None:
                bed_y_min = 0.0
            if bed_y_max is None:
                bed_y_max = 220.0

    k = math.tan(math.radians(skew_deg))

    # 2. Compute y_ref from original in-bed extruding bounds
    if shear_y_ref_mode == "auto":
        _, _, ominy, omaxy = compute_inbed_extruding_bounds_original(
            gf.lines, bed_x_min, bed_x_max, bed_y_min, bed_y_max,
        )
        y_ref = 0.5 * (ominy + omaxy) if (ominy != 0.0 or omaxy != 0.0) else 0.0
    else:
        y_ref = float(shear_y_ref)

    # 3. Compute recenter translation
    dx = dy = 0.0
    skew_bounds = (0.0, 0.0, 0.0, 0.0)
    if recenter:
        dx, dy, skew_bounds = compute_translation_for_bounds(
            gf.lines, k, y_ref,
            bed_x_min, bed_x_max, bed_y_min, bed_y_max,
            margin, recenter_mode,
        )

    # 4. Analyze-only path
    if analyze_only:
        report = analyze_gcode(
            gf.lines, k, y_ref,
            bed_x_min, bed_x_max, bed_y_min, bed_y_max,
            recenter, margin, recenter_mode,
        )
        for line in report:
            print(line)
        return

    # 5. Transform pipeline: linearize arcs -> apply skew -> translate
    lines = gcode_lib.linearize_arcs(
        gf.lines,
        xy_decimals=xy_decimals,
        other_decimals=other_decimals,
    )
    try:
        lines = gcode_lib.apply_skew(
            lines, skew_deg, y_ref,
            xy_decimals=xy_decimals,
            other_decimals=other_decimals,
        )
        if dx != 0.0 or dy != 0.0:
            lines = gcode_lib.translate_xy(
                lines, dx, dy,
                xy_decimals=xy_decimals,
                other_decimals=other_decimals,
            )
    except ValueError as exc:
        raise SystemExit(
            "prusaslicer-skew-fix: ERROR: relative XY (G91) is not supported. "
            "PrusaSlicer emits absolute XY (G90) toolpaths; relative XY would "
            "make skew compensation ambiguous."
        ) from exc

    # 6. Prepend metadata comments
    metadata = _build_metadata_lines(
        skew_deg, k, y_ref, shear_y_ref_mode,
        xy_decimals, other_decimals,
        recenter, recenter_mode, margin, skew_bounds, dx, dy,
    )
    gf.lines = metadata + lines

    # 7. Save (atomic, preserves format)
    gcode_lib.save(gf, path)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

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
    ap.add_argument("--bed-x-min", type=_finite_float, default=None,
                    help="Bed X minimum (default: auto-detect from G-code, else 0).")
    ap.add_argument("--bed-x-max", type=_finite_float, default=None,
                    help="Bed X maximum (default: auto-detect from G-code, else 250).")
    ap.add_argument("--bed-y-min", type=_finite_float, default=None,
                    help="Bed Y minimum (default: auto-detect from G-code, else 0).")
    ap.add_argument("--bed-y-max", type=_finite_float, default=None,
                    help="Bed Y maximum (default: auto-detect from G-code, else 220).")
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
    if a.bed_x_min is not None and a.bed_x_max is not None and a.bed_x_min > a.bed_x_max:
        ap.error("--bed-x-min must be <= --bed-x-max.")
    if a.bed_y_min is not None and a.bed_y_max is not None and a.bed_y_min > a.bed_y_max:
        ap.error("--bed-y-min must be <= --bed-y-max.")

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
        xy_decimals=a.xy_decimals,
        other_decimals=a.other_decimals,
    )

if __name__ == "__main__":
    main(sys.argv[1:])
