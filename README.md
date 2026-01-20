# prusaslicer-skew-fix

**XY skew correction for PrusaSlicer when firmware `M852` is unavailable.**

This repository provides a PrusaSlicer post-processing script that applies XY skew (shear)
correction directly to generated **text** G-code.

## What it does

- Applies XY skew correction using a shear:
  ```
  x' = x + y * tan(theta)
  y' = y
  ```
- Optionally linearizes arcs (`G2`/`G3`) into `G1` segments (required for correct skew)
- Optionally recenters to prevent clipping **without being fooled by purge/wipe code**

## IMPORTANT: Binary G-code

This script **does not** support Prusa Binary G-code (`.bgcode`, magic `GCDE`).
Disable Binary G-code output in PrusaSlicer so a text `.gcode` is generated.

## Arcs / circles (G2/G3)

A shear transform does **not** preserve circles (circles become ellipses).

If your G-code contains `G2`/`G3`, enable arc linearization:

```
python3 skew_fix_ps.py --skew-deg -0.15 --linearize-arcs
```

You can control the smoothness with:
- `--arc-segment-mm` (default 0.20 mm)
- `--arc-max-deg` (default 5°)

## Prevent clipping: recenter using **extruding in-bed bounds** (recommended)

Skew correction can move geometry slightly in X. To prevent the part from clipping,
enable `--recenter-to-bed`.

**How bounds are computed (important):**
- Only moves that **extrude** (E increases / E > 0) are considered “model” bounds
- Only endpoints that are already **inside the bed** in the original G-code are included
- Purge lines / nozzle wipers / parking moves outside the bed are ignored by design

This means your custom purge/wipe macros won’t cause confusing shifts of the actual model.

Example (Core One defaults, small margin):

```
python3 skew_fix_ps.py --skew-deg -0.15 --linearize-arcs --recenter-to-bed --margin 0.2
```

### Recenter mode: `center` vs `clamp`

- `--recenter-mode center` (default): place the model in the middle of the allowable range
- `--recenter-mode clamp`: minimal movement from 0 shift (only move if needed)

Example:

```
python3 skew_fix_ps.py --skew-deg -0.15 --linearize-arcs --recenter-to-bed --recenter-mode clamp
```

### Tolerance (`--eps`)

The bounds check uses a small tolerance (default `--eps 0.01`) to avoid failing due to tiny
floating-point rounding differences.

## PrusaSlicer setup

Add to **Print Settings → Output options → Post-processing scripts**:

```
python3 /path/to/skew_fix_ps.py --skew-deg -0.15 --linearize-arcs --recenter-to-bed
```

(Do **not** add `[output_filepath]` — PrusaSlicer supplies the path automatically.)

## Notes

- Absolute XY (`G90`) is required for recentering (default PrusaSlicer output)
- Header comment includes computed bounds and applied translation

## License

MIT
