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
- Optionally recenters the toolpath to prevent clipping

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

## Prevent clipping: recenter using **model-only bounds** (recommended)

Skew correction can move geometry slightly in X.

When `--recenter-to-bed` is enabled, the script:

1. Computes skewed bounds using **ONLY in-bed geometry**
   - Purge lines, wipe moves, and parking moves that are *already outside the bed* are ignored
2. Translates the entire toolpath so the **model** remains inside the bed
3. Aborts only if the model still cannot fit

This avoids large, confusing shifts caused by purge/wipe moves.

Example (Core One defaults, small margin):

```
python3 skew_fix_ps.py --skew-deg -0.15 --linearize-arcs --recenter-to-bed --margin 0.2
```

If your bed differs:

```
python3 skew_fix_ps.py --skew-deg -0.15 --recenter-to-bed --bed-x-max 250 --bed-y-max 220
```

## PrusaSlicer setup

Add to **Print Settings → Output options → Post-processing scripts**:

```
python3 /path/to/skew_fix_ps.py --skew-deg -0.15 --linearize-arcs --recenter-to-bed
```

(Do **not** add `[output_filepath]` — PrusaSlicer supplies the path automatically.)

## Notes

- Absolute XY (`G90`) is required for recentering (default PrusaSlicer output)
- Purge / wipe macros outside the bed are intentionally ignored for bounds
- Header comments include computed in-bed bounds and applied translation

## License

MIT
