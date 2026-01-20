# prusaslicer-skew-fix

**XY skew correction for PrusaSlicer when firmware `M852` is unavailable.**

This repo provides a PrusaSlicer post-processing script that applies XY skew (shear) compensation
directly to generated **text** G-code, with:

- optional **arc linearization** (`G2/G3` → `G1` segments)
- optional **auto-recenter + bounds check** to prevent clipping after skew correction

Prusa/Buddy firmware does not support `M852` skew correction (see discussion: GitHub issue #385).

## Skew model

```
x' = x + y * tan(theta)
y' = y
```

`theta` is the measured XY skew angle in degrees (e.g. from Califlower V2).

## PrusaSlicer setup

In **Print Settings → Output options → Post-processing scripts**, add:

```
python3 /path/to/skew_fix_ps.py --skew-deg -0.15
```

(Do **not** add `[output_filepath]` — PrusaSlicer supplies the path automatically.)

## IMPORTANT: PrusaConnect and Binary G-code

If you send jobs via **PrusaConnect**, PrusaSlicer may be configured to output **Binary G-code**
(often `.bgcode`, magic header `GCDE`). This script **cannot** edit binary files.

✅ Fix: **Disable “Binary G-code” output** in your printer/profile so PrusaSlicer produces a text `.gcode`.
Then re-slice.

## Arcs / circles (G2/G3)

A shear transform does **not** preserve circles (a circle becomes an ellipse), so `G2/G3` arcs must be
**converted to line segments** for correct skew compensation.

Enable arc linearization:

```
python3 /path/to/skew_fix_ps.py --skew-deg -0.15 --linearize-arcs --arc-segment-mm 0.20 --arc-max-deg 5
```

- `--arc-segment-mm`: max chord length (smaller = smoother, larger output file)
- `--arc-max-deg`: max degrees per segment

## Prevent clipping: auto-recenter + bounds checking (recommended)

Skew correction can shift coordinates enough to push the toolpath outside the printable area.
To prevent clipping, enable `--recenter-to-bed`. The script will:

1) compute the skewed XY bounds,
2) translate the entire toolpath to keep it inside the bed (optionally with a margin),
3) abort if it still cannot fit.

Example (Core One-like defaults 250×220 mm):

```
python3 /path/to/skew_fix_ps.py --skew-deg -0.15 --recenter-to-bed --margin 0.5
```

If your bed differs, override bounds:

```
python3 /path/to/skew_fix_ps.py --skew-deg -0.15 --recenter-to-bed --bed-x-max 250 --bed-y-max 220 --margin 0.5
```

Notes:
- `--recenter-to-bed` currently requires **absolute XY** moves (`G90`). PrusaSlicer output is typically absolute.
- The script writes a header comment showing the computed skewed bounds and applied translation.

## Verification

Look for this header in the output G-code:

```
; postprocess: prusaslicer-skew-fix --skew-deg ...
```

## License

MIT
