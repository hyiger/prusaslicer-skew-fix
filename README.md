# prusaslicer-skew-fix

XY skew correction for PrusaSlicer **when firmware M852 is not available**
(e.g. Prusa Core One). This is a slicer-side post-processing script that
modifies **text G-code** after slicing.

---

## Measuring skew

See [MEASURING_SKEW.md](MEASURING_SKEW.md) for recommended ways to measure XY skew (including Califlower v2 and generic methods).

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

- `y_ref` is the shear reference line (see below).

### Shear reference (`y_ref`)

The script applies skew relative to a horizontal reference line:

```
x' = x + (y - y_ref) * tan(theta)
y' = y
```

**Default (`--shear-y-ref-mode auto`)**  
`y_ref` is computed as the **center of extruding Y motion** (based on moves that actually print plastic). This makes the induced X displacement more symmetric and reduces the chance of pushing geometry toward a bed edge on large parts.

**Legacy / Marlin-global-origin equivalent**  
To reproduce older releases of this tool (and a global-origin shear), use:

```bash
--shear-y-ref-mode fixed --shear-y-ref 0
```

### Worked numeric example

Given:
- `theta = -0.15°`
- `y_ref = 100 mm`
- point `(x, y) = (50, 200)`

Then:

```
x' = 50 + (200 - 100) * tan(-0.15°)
   ≈ 49.738
y' = 200
```


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

If your G-code contains `G2` or `G3`, you **must** enable arc linearization (convert arcs into short `G1` segments):

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
With `--recenter-to-bed`, the script can **translate the toolpath in XY to fit the printable bed**.
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

## Output formatting (decimal places)

By default the script emits:

- X/Y with **3** decimals (`--xy-decimals 3`)
- Other axes (E/F/Z/I/J/K/...) with **5** decimals (`--other-decimals 5`)

This significantly reduces file size and keeps the output well within mechanical resolution.

## Backward compatibility

If you need output that is geometrically identical to older versions of this tool, use:

```bash
--shear-y-ref-mode fixed --shear-y-ref 0 --xy-decimals 5 --other-decimals 5
```

This reproduces the global-origin shear reference and higher-precision formatting used by earlier releases.


## Recommended PrusaSlicer setup

**Print Settings → Output options → Post-processing scripts**

```
python3 /path/to/skew_fix_ps.py   --skew-deg -0.15   --shear-y-ref-mode auto   --linearize-arcs   --recenter-to-bed   --recenter-mode clamp
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

```bash
python3 skew_fix_ps.py --skew-deg -0.15 --shear-y-ref-mode auto --recenter-to-bed --recenter-mode clamp --analyze-only /path/to/file.gcode
```

The output includes:

- Pre/post XY move bounds
- Maximum |ΔX|
- (If recenter is enabled) the computed in-bed extruding skewed bounds and translation


**Note:** `--analyze-only` does not write an output file.

Sample output (abridged):

```text
Input bounds (extruding XY):   X[...,...]  Y[...,...]
Skewed bounds (before shift):  X[...,...]  Y[...,...]
Max |ΔX|: ...
Recenter shift applied:        ΔX=...  ΔY=...
Final bounds (in bed):         X[...,...]  Y[...,...]
```
