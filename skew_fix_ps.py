#!/usr/bin/env python3
"""
PrusaSlicer post-processing hook: applies XY skew correction to a generated .gcode file.

Model:
    x' = x + y * tan(theta)
    y' = y

Replacement for firmware-level M852 skew correction on Prusa/Buddy firmware.

Binary G-code guard:
- If the input file is Prusa "Binary G-code" (.bgcode) (magic header 'GCDE') or
  appears to be binary (NUL bytes early), this script aborts with a clear error
  to avoid corrupting the file. Disable "Binary G-code" output in PrusaSlicer to use this tool.
"""

from __future__ import annotations
import argparse
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass

MOVE_RE = re.compile(r"^(G0|G1)\b", re.IGNORECASE)
AXIS_RE = re.compile(r"([XYZEFR])\s*(-?\d+(?:\.\d*)?|-?\.\d+)", re.IGNORECASE)

@dataclass
class State:
    abs_xy: bool = True
    x: float = 0.0
    y: float = 0.0

def split_comment(line: str):
    if ";" in line:
        code, comment = line.split(";", 1)
        return code.rstrip(), ";" + comment
    return line.rstrip(), ""

def parse_axes(code: str):
    return {m.group(1).upper(): float(m.group(2)) for m in AXIS_RE.finditer(code)}

def fmt(v: float) -> str:
    s = f"{v:.5f}".rstrip("0").rstrip(".")
    return s if s else "0"

def replace_axis(code: str, axis: str, val: float) -> str:
    axis = axis.upper()
    tok = f"{axis}{fmt(val)}"
    pat = re.compile(rf"(?i)\b{axis}\s*(-?\d+(?:\.\d*)?|-?\.\d+)\b")
    return pat.sub(tok, code, 1) if pat.search(code) else (code + " " + tok)

def process(line: str, st: State, tan_t: float) -> str:
    raw = line.rstrip("\n")
    code, comment = split_comment(raw)
    s = code.strip()
    if not s:
        return raw

    up = s.upper()
    if up.startswith("G90"):
        st.abs_xy = True
        return raw
    if up.startswith("G91"):
        st.abs_xy = False
        return raw

    if not MOVE_RE.match(s):
        return raw

    axes = parse_axes(code)
    has_x = "X" in axes
    has_y = "Y" in axes
    if not (has_x or has_y):
        return raw

    if st.abs_xy:
        x = axes.get("X", st.x)
        y = axes.get("Y", st.y)
        x2 = x + y * tan_t
        new = code
        if has_x:
            new = replace_axis(new, "X", x2)
        if has_y:
            new = replace_axis(new, "Y", y)
        st.x, st.y = x, y
    else:
        dx = axes.get("X", 0.0)
        dy = axes.get("Y", 0.0)
        dx2 = dx + dy * tan_t
        new = code
        if has_x:
            new = replace_axis(new, "X", dx2)
        if has_y:
            new = replace_axis(new, "Y", dy)
        st.x += dx
        st.y += dy

    return new.rstrip() + ("" if not comment else " " + comment.lstrip())

def _assert_text_gcode(path: str) -> None:
    with open(path, "rb") as f:
        head = f.read(512)

    # Prusa Binary G-code magic is 'GCDE' near file start
    if head.startswith(b"GCDE") or b"GCDE" in head[:64]:
        raise SystemExit(
            "prusaslicer-skew-fix: ERROR: Binary G-code detected (magic 'GCDE').\n"
            "This script only supports text .gcode.\n"
            "Fix: Disable 'Binary G-code' output in PrusaSlicer, then re-slice."
        )

    # Text G-code should not contain NUL bytes
    if b"\x00" in head:
        raise SystemExit(
            "prusaslicer-skew-fix: ERROR: File appears to be binary (NUL bytes detected).\n"
            "This script only supports text .gcode.\n"
            "Fix: Disable 'Binary G-code' output in PrusaSlicer, then re-slice."
        )

def rewrite(path: str, skew_deg: float) -> None:
    _assert_text_gcode(path)

    tan_t = math.tan(math.radians(skew_deg))
    st = State()

    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out,              open(path, "r", encoding="utf-8", errors="replace") as inp:
            out.write(f"; postprocess: prusaslicer-skew-fix --skew-deg {skew_deg}\n")
            for line in inp:
                out.write(process(line, st, tan_t) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--skew-deg", type=float, required=True, help="XY skew angle in degrees (e.g. -0.15)")
    ap.add_argument("gcode", help="Path to generated .gcode (PrusaSlicer supplies this)")
    a = ap.parse_args(argv)
    rewrite(a.gcode, a.skew_deg)

if __name__ == "__main__":
    main(sys.argv[1:])
