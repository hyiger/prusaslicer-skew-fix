# prusaslicer-skew-fix

XY skew correction for PrusaSlicer **when firmware M852 is not available**
(e.g. Prusa Core One). This is a slicer-side post-processing script that
modifies **text G-code** after slicing.

---

## Measuring skew

See [MEASURING_SKEW.md](MEASURING_SKEW.md) for recommended ways to measure XY skew (including Califlower v2 and generic methods).

---


## Deriving skew from a printed square or rectangle

If you prefer to derive skew from simple caliper measurements (no angle math), you can use:

```
--skew-from-square AC,BD,AD
--skew-from-rectangle AC,BD,AD,AB
```

Label your printed square/rectangle like this:

```
A -------- B
|          |
|          |
D -------- C
```

- **AC**: diagonal A→C  
- **BD**: diagonal B→D  
- **AD**: Y-direction side length  
- **AB**: X-direction side length (rectangles)

The derived skew matches the same shear model as Marlin `M852`:

	tan(theta) = (AC - BD) / (2 * AD)


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

### Coordinate conventions

- The correction is a **shear in X as a function of Y** (Y is unchanged).
- The transform is applied to the **endpoint position** of each XY move.
- For correctness, the rewriter may **add a missing X or Y word** on a `G0`/`G1` line (for example, a `G1 Y...` line still changes X under shear, because `x'` depends on `y`).

### Shear reference (`y_ref`)

The script applies skew relative to a horizontal reference line:

```
x' = x + (y - y_ref) * tan(theta)
y' = y
```

**Default (`--shear-y-ref-mode auto`)**  
`y_ref` is computed as the **center of extruding Y motion** (based on moves that actually print plastic). This makes the induced X displacement more symmetric and reduces the chance of pushing geometry toward a bed edge on large parts.


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
Arc linearization is ON by default; arc linearization is always enabled
```

Defaults:
- `- `
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

By default, for motion lines it rewrites (`G0`/`G1`, including any arc-linearized `G1` segments), the script:

- Rounds X/Y to **3** decimal places (`--xy-decimals 3`)
- Rounds other axes (E/F/Z/...) to **5** decimal places (`--other-decimals 5`)

Trailing zeros are trimmed (e.g. `Y10.000` becomes `Y10`), which keeps the output compact while preserving the requested precision.

## Recommended PrusaSlicer setup

**Print Settings → Output options → Post-processing scripts**

```
python3 skew_fix_ps.py --skew-deg -0.15 --shear-y-ref-mode auto --recenter-to-bed --recenter-mode clamp
```

Do **not** add `[output_filepath]` — PrusaSlicer supplies it automatically.

---

## Assumptions and limitations

- Absolute XY positioning (`G90`) required
- Text G-code only
- Intended for small-angle skew correction
- Z coordinates are not modified

## When not to use this

- If your firmware already supports and is using `M852` (or another skew/orthogonality correction) — don’t double-correct.
- If you already corrected the model STL in CAD or with a mesh transform — don’t apply this again.
- If your measured skew is within your measurement noise (for many setups, ~0.02° or less) — you may just be adding complexity.

---

## Correctness-first output

- If a G0/G1 move specifies **either** X or Y, the script will emit **both** X and Y in the rewritten line.
  This is required because the shear transform is defined in absolute coordinates and `x'` depends on `y`.
- Relative XY (G91) output is rejected.

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


### Arc handling
Arc linearization is **always enabled** and uses fixed, correctness-first parameters:
- segment length: 0.20 mm
- max angle per segment: 5.0°
These values are not configurable to avoid incorrect toolpaths.


## Non-goals

This tool is intentionally **not** a general-purpose G-code motion planner.
The following behaviors are explicitly out of scope:

- **Relative XY motion (G91)**  
  PrusaSlicer always emits absolute XY coordinates (G90) for print toolpaths.
  Supporting relative XY would require full motion-state replay and would make
  skew correction ambiguous. Files containing G91 XY motion are rejected.

- Preserving firmware-level arc commands (G2/G3)  
  All arcs are always linearized to G1 segments before skew compensation.

- Minimizing G-code size  
  Correctness and dimensional accuracy take priority over file size.

- Backwards compatibility with older versions of this tool  
  Tests and behavior lock in current design decisions.
