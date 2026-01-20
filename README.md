# prusaslicer-skew-fix

XY skew correction for PrusaSlicer **when firmware M852 is not available**
(e.g. Prusa Core One). This is a slicer-side post-processing script that
modifies **text G-code** after slicing.

---

## What problem this solves

If your printer has measurable XY skew (axes not perfectly orthogonal),
parts will be dimensionally inaccurate even with good calibration.

On Marlin-based firmware this is normally fixed with `M852`, but Prusa
Buddy firmware does not support that command. This script applies the
same correction *after slicing*, without modifying firmware.

---

## Math overview

Skew is modeled as an affine shear in X proportional to Y:

```
x' = x + y * tan(theta)
y' = y
```

- `theta` is the measured XY skew angle (for example `-0.15°` from confirmatory tests like Califlower)
- This matches the math used by Marlin’s `M852`

---

## Key features

- Applies correct XY skew compensation
- Optional arc linearization (`G2`/`G3` → `G1`)
- Safe recentering to prevent bed clipping
- Bounds computed from **actual printed geometry only**
- Purge / wipe / parking moves ignored by design
- Guard against Prusa **binary G-code (.bgcode)**

---

## IMPORTANT: Binary G-code

This script only supports **text `.gcode`**.

If PrusaSlicer outputs `.bgcode`:
- Disable *Binary G-code* in PrusaSlicer
- Re-slice

The script will abort if binary G-code is detected to prevent file corruption.

---

## Arcs and circles (important)

A shear transform does **not** preserve circles — circles become ellipses.

If your G-code contains `G2` or `G3`, you **must** enable arc linearization:

```
--linearize-arcs
```

Defaults:
- `--arc-segment-mm 0.20`
- `--arc-max-deg 5.0`

This avoids preview artifacts and ensures printed geometry matches the math.

---

## Recenter logic (preventing clipping)

After skew correction, geometry may shift slightly in X.
To ensure nothing goes out of bounds:

```
--recenter-to-bed
```

### How bounds are computed

Bounds include:
- Moves that **extrude plastic**
- Endpoints already **inside the printable bed**

Bounds explicitly exclude:
- Purge lines
- Nozzle wipers
- Parking moves
- Travel-only moves

This avoids confusing shifts caused by startup or maintenance macros.

---

## Recenter modes

```
--recenter-mode center   # default
--recenter-mode clamp
```

- `center`: place the model in the middle of the valid range
- `clamp`: apply the *minimum* shift required to stay in bounds

`clamp` is recommended for predictable placement.

---

## Floating-point tolerance

```
--eps 0.01
```

This prevents false “cannot fit” errors caused by floating-point rounding.

---

## Recommended PrusaSlicer setup

**Print Settings → Output options → Post-processing scripts**

```
python3 /path/to/skew_fix_ps.py   --skew-deg -0.15   --linearize-arcs   --recenter-to-bed   --recenter-mode clamp
```

Do **not** add `[output_filepath]` — PrusaSlicer supplies it automatically.

---

## Assumptions and limitations

- Absolute XY positioning (`G90`) required
- Text G-code only
- Intended for small-angle skew correction
- Z coordinates are not modified

---

## License

MIT

---

## Analyze-only mode

You can inspect skew effects **without modifying the G-code**:

```
--analyze-only
```

This reports:
- Maximum theoretical X displacement from skew
- Skewed model bounds
- Recommended recenter translation (if enabled)

Useful for sanity-checking skew values before applying them.
