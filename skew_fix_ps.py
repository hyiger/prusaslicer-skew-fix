#!/usr/bin/env python3
"""PrusaSlicer post-processing hook: applies XY skew correction to a generated .gcode file.

Model (default):
    x' = x + y * tan(theta)
    y' = y

Designed for PrusaSlicer/Marlin-style G-code (Prusa Core One / Buddy firmware).

Usage (PrusaSlicer post-processing scripts):
  python3 /path/to/skew_fix_ps.py --skew-deg -0.15 -- "[output_filepath]"

Notes:
- Accepts the gcode file path as the final argument. PrusaSlicer substitutes [output_filepath].
- Rewrites the file in-place safely (temp file + atomic replace).
- Tracks G90/G91 for XY.
- Modifies only G0/G1 XY words; leaves E/Z/F/etc. untouched.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass

MOVE_RE = re.compile(r"^(?P<cmd>G0|G1)\b", re.IGNORECASE)
AXIS_RE = re.compile(r"([XYZEFR])\s*(-?\d+(?:\.\d*)?|-?\.\d+)", re.IGNORECASE)


@dataclass
class State:
    abs_xy: bool = True
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    e: float = 0.0


def split_comment(line: str) -> tuple[str, str]:
    """Split a G-code line into (code, comment) preserving ';' comments."""
    if ";" in line:
        code, comment = line.split(";", 1)
        return code.rstrip(), ";" + comment
    return line.rstrip(), ""


def parse_axes(code: str) -> dict[str, float]:
    """Extract axis words like X12.3 Y-4.0 from the code portion."""
    axes: dict[str, float] = {}
    for m in AXIS_RE.finditer(code):
        axes[m.group(1).upper()] = float(m.group(2))
    return axes


def format_number(v: float) -> str:
    """Stable-ish formatting for numeric rewrites."""
    s = f"{v:.5f}".rstrip("0").rstrip(".")
    return s if s else "0"


def replace_axis(code: str, axis: str, new_val: float) -> str:
    """Replace an existing axis word (e.g., X...) with a new value."""
    axis = axis.upper()
    new_tok = f"{axis}{format_number(new_val)}"
    pattern = re.compile(rf"(?i)\b{axis}\s*(-?\d+(?:\.\d*)?|-?\.\d+)\b")
    if pattern.search(code):
        return pattern.sub(new_tok, code, count=1)
    # If axis is missing, append (normally we avoid adding missing axes, but keep for robustness)
    return (code + " " + new_tok).rstrip()


def process_line(line: str, st: State, tan_theta: float) -> str:
    raw = line.rstrip("\n")
    code, comment = split_comment(raw)
    stripped = code.strip()

    if not stripped:
        return raw

    up = stripped.upper()

    # Track absolute/relative positioning
    if up.startswith("G90"):
        st.abs_xy = True
        return raw
    if up.startswith("G91"):
        st.abs_xy = False
        return raw

    # Only handle linear moves
    if not MOVE_RE.match(stripped):
        return raw

    axes = parse_axes(code)
    has_x = "X" in axes
    has_y = "Y" in axes

    if not (has_x or has_y):
        # Nothing to do if neither X nor Y are present
        return raw

    if st.abs_xy:
        x_target = axes["X"] if has_x else st.x
        y_target = axes["Y"] if has_y else st.y

        # Apply skew correction
        x_corr = x_target + y_target * tan_theta
        y_corr = y_target

        new_code = code
        # Only rewrite axes that exist on the line (don’t add missing axes)
        if has_x:
            new_code = replace_axis(new_code, "X", x_corr)
        if has_y:
            new_code = replace_axis(new_code, "Y", y_corr)

        # Track logical (uncorrected) commanded coordinates
        st.x = x_target
        st.y = y_target

    else:
        dx = axes["X"] if has_x else 0.0
        dy = axes["Y"] if has_y else 0.0

        # Apply skew correction to the move vector
        dx_corr = dx + dy * tan_theta
        dy_corr = dy

        new_code = code
        if has_x:
            new_code = replace_axis(new_code, "X", dx_corr)
        if has_y:
            new_code = replace_axis(new_code, "Y", dy_corr)

        st.x += dx
        st.y += dy

    rebuilt = new_code.rstrip()
    if comment:
        rebuilt += " " + comment.lstrip()
    return rebuilt.rstrip()


def rewrite_in_place(path: str, skew_deg: float) -> None:
    theta = math.radians(skew_deg)
    tan_theta = math.tan(theta)

    st = State()

    abs_path = os.path.abspath(path)
    dir_name = os.path.dirname(abs_path) or "."
    base_name = os.path.basename(abs_path)

    fd, tmp_path = tempfile.mkstemp(prefix=base_name + ".", suffix=".tmp", dir=dir_name, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f_out, open(
            abs_path, "r", encoding="utf-8", errors="replace"
        ) as f_in:
            # Stamp a header note (helps verify the hook ran)
            f_out.write(f"; postprocess: skew_fix_ps.py --skew-deg {skew_deg}\n")

            for line in f_in:
                f_out.write(process_line(line, st, tan_theta) + "\n")

        os.replace(tmp_path, abs_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--skew-deg", type=float, required=True, help="XY skew angle in degrees (e.g. -0.15)")
    ap.add_argument(
        "gcode_path",
        help='Path to .gcode file (PrusaSlicer passes this as "[output_filepath]")',
    )
    args = ap.parse_args(argv)

    rewrite_in_place(args.gcode_path, args.skew_deg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
