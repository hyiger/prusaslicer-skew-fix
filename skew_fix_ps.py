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

def split_comment(line: str) -> Tuple[str, str]:
    if ";" in line:
        code, comment = line.split(";", 1)
        return code.rstrip(), ";" + comment
    return line.rstrip(), ""

def parse_words(code: str) -> Dict[str, float]:
    return {m.group(1).upper(): float(m.group(2)) for m in AXIS_RE.finditer(code)}

def fmt(v: float) -> str:
    s = f"{v:.5f}".rstrip("0").rstrip(".")
    return s if s else "0"

def replace_or_append(code: str, axis: str, val: float) -> str:
    axis = axis.upper()
    tok = f"{axis}{fmt(val)}"
    pat = re.compile(rf"(?i)\b{axis}\s*(-?\d+(?:\.\d*)?|-?\.\d+)\b")
    return pat.sub(tok, code, 1) if pat.search(code) else (code + " " + tok)

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

def apply_skew_abs(x: float, y: float, k: float) -> Tuple[float, float]:
    return (x + y * k, y)

def _arc_center(st: State, words: Dict[str, float]) -> Tuple[float, float]:
    I = words.get("I", 0.0)
    J = words.get("J", 0.0)
    if st.ij_relative:
        return (st.x + I, st.y + J)
    return (I, J)

def _arc_end_abs(st: State, words: Dict[str, float]) -> Tuple[float, float]:
    if st.abs_xy:
        x1 = words.get("X", st.x)
        y1 = words.get("Y", st.y)
    else:
        x1 = st.x + words.get("X", 0.0)
        y1 = st.y + words.get("Y", 0.0)
    return x1, y1

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

def _in_bed(x: float, y: float, xmin: float, xmax: float, ymin: float, ymax: float) -> bool:
    return (xmin <= x <= xmax) and (ymin <= y <= ymax)

def _is_extruding_move(st: State, words: Dict[str, float]) -> bool:
    if "E" not in words:
        return False
    e_word = words["E"]
    if st.abs_e:
        return e_word > st.e
    return e_word > 0.0

def _choose_translation(lo: float, hi: float, mode: str) -> float:
    if mode == "center":
        return 0.5 * (lo + hi)
    if lo <= 0.0 <= hi:
        return 0.0
    return lo if abs(lo) < abs(hi) else hi

def compute_translation_for_bounds(path: str, k: float, linearize: bool,
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
                                xs, ys = apply_skew_abs(xi, yi, k)
                                upd(xs, ys)
                    else:
                        x1, y1 = _arc_end_abs(st, words)
                        if _in_bed(x1, y1, bed_x_min, bed_x_max, bed_y_min, bed_y_max):
                            xs, ys = apply_skew_abs(x1, y1, k)
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
                    xs, ys = apply_skew_abs(x1, y1, k)
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

def rewrite(path: str, skew_deg: float,
            linearize: bool, arc_seg_mm: float, arc_max_deg: float,
            recenter: bool, bed_x_min: float, bed_x_max: float,
            bed_y_min: float, bed_y_max: float, margin: float,
            recenter_mode: str, eps: float) -> None:
    _assert_text_gcode(path)
    k = math.tan(math.radians(skew_deg))
    st = State()

    dx = dy = 0.0
    skew_bounds = (0.0, 0.0, 0.0, 0.0)
    if recenter:
        dx, dy, skew_bounds = compute_translation_for_bounds(
            path, k, linearize, arc_seg_mm, arc_max_deg,
            bed_x_min, bed_x_max, bed_y_min, bed_y_max, margin,
            recenter_mode, eps
        )

    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out, \
             open(path, "r", encoding="utf-8", errors="replace") as inp:

            hdr = f"; postprocess: prusaslicer-skew-fix --skew-deg {skew_deg}"
            if linearize:
                hdr += f" --linearize-arcs --arc-segment-mm {arc_seg_mm} --arc-max-deg {arc_max_deg}"
            if recenter:
                hdr += f" --recenter-to-bed --recenter-mode {recenter_mode} --bed-x-min {bed_x_min} --bed-x-max {bed_x_max} --bed-y-min {bed_y_min} --bed-y-max {bed_y_max} --margin {margin} --eps {eps}"
                minx, maxx, miny, maxy = skew_bounds
                hdr += f" ; in-bed extruding skewed bounds: X[{minx:.3f},{maxx:.3f}] Y[{miny:.3f},{maxy:.3f}] shift: dx={dx:.3f} dy={dy:.3f}"
            out.write(hdr + "\n")

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
                            xs, ys = apply_skew_abs(xi, yi, k)
                            xs += dx; ys += dy
                            l = "G1"
                            l += f" X{fmt(xs)} Y{fmt(ys)}"
                            if has_e:
                                if st.abs_e:
                                    t = i / len(pts)
                                    ei = e0 + dE * t
                                    if i == len(pts): ei = e_end
                                    l += f" E{fmt(ei)}"
                                else:
                                    l += f" E{fmt(dE / len(pts))}"
                            if has_f and f_word is not None and i == 1:
                                l += f" F{fmt(f_word)}"
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
                        xs, ys = apply_skew_abs(x_t, y_t, k)
                        xs += dx; ys += dy
                        new = code
                        if has_x: new = replace_or_append(new, "X", xs)
                        if has_y: new = replace_or_append(new, "Y", ys)
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

def main(argv: List[str]) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skew-deg", type=float, required=True, help="XY skew angle in degrees (e.g. -0.15)")

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
            a.recenter_mode, a.eps)

if __name__ == "__main__":
    main(sys.argv[1:])
