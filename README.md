# prusaslicer-skew-fix

XY skew correction for PrusaSlicer when firmware skew correction (`M852`) is unavailable
(for example, Prusa Buddy firmware / Core One).

This script is a post-processing step that rewrites **text `.gcode`** after slicing.

## Quick Start

1. Measure skew (recommended: Califlower v2). See [`MEASURING_SKEW.md`](MEASURING_SKEW.md).
2. In PrusaSlicer, set a post-processing script:

```bash
python3 skew_fix_ps.py --skew-deg -0.15 --shear-y-ref-mode auto --recenter-to-bed --recenter-mode clamp
```

Do not add `[output_filepath]`; PrusaSlicer appends the generated file path automatically.

## What It Solves

If XY axes are not perfectly orthogonal, printed parts can be dimensionally skewed.
Marlin firmware typically fixes this with `M852`; this tool applies the equivalent correction in G-code instead.

## CLI Reference

```text
skew_fix_ps.py [options] gcode
```

- `gcode` (positional): path to generated text `.gcode`
- Skew source (choose exactly one):
- `--skew-deg SKEW_DEG`
- `--skew-from-square AC,BD,AD`
- `--skew-from-rectangle AC,BD,AD,AB`
- `--shear-y-ref-mode {auto,fixed}` (default: `auto`)
- `--shear-y-ref SHEAR_Y_REF` (used when mode is `fixed`)
- `--xy-decimals XY_DECIMALS` (default: `3`)
- `--other-decimals OTHER_DECIMALS` (default: `5`)
- `--analyze-only`
- `--recenter-to-bed`
- `--recenter-mode {center,clamp}` (default: `center`)
- `--bed-x-min` (default: `0`)
- `--bed-x-max` (default: `250`)
- `--bed-y-min` (default: `0`)
- `--bed-y-max` (default: `220`)
- `--margin` (default: `0`, must be non-negative)

## Core Behavior

### Transform model

The correction is an affine shear in X as a function of Y:

```text
x' = x + (y - y_ref) * tan(theta)
y' = y
```

- `theta` is skew angle in degrees.
- `y_ref` is the shear reference line.
- With `--shear-y-ref-mode auto`, `y_ref` is chosen as the center of in-bed extruding Y motion.

Legacy/global-origin equivalent:

```bash
--shear-y-ref-mode fixed --shear-y-ref 0
```

### Coordinate conventions

- Transform is applied to absolute XY endpoints.
- For `G0`/`G1` lines that include either X or Y, output may include both X and Y because `x'` depends on `y`.
- Relative XY (`G91`) is rejected for rewrite output.

### Arc handling (always enabled)

Shear does not preserve circles, so `G2`/`G3` arcs are always linearized to `G1` segments before skew:

- segment length: `0.20 mm`
- max angle per segment: `5.0°`

### Recenter behavior

With `--recenter-to-bed`, the script computes optional XY translation to keep skewed geometry in bed limits.

Bounds are based on **in-bed extruding geometry only**:

- included: extruding moves (including extruding arc segments)
- excluded: purge/wipe/parking/travel-only moves

Modes:

- `center`: midpoint of allowable translation interval
- `clamp`: minimum shift needed (prefers `0` when valid)

`clamp` is usually more predictable for placement.

### Output formatting

For rewritten motion lines:

- X/Y use `--xy-decimals` (default `3`)
- other axes/words use `--other-decimals` (default `5`)
- trailing zeros are trimmed

## Analyze-Only Mode

Inspect effects without modifying the file:

```bash
python3 skew_fix_ps.py --skew-deg -0.15 --shear-y-ref-mode auto --recenter-to-bed --recenter-mode clamp --analyze-only /path/to/file.gcode
```

Reports include pre/post bounds, max `|ΔX|`, and recenter shift (when enabled).

## Safety and File Handling

- Text G-code only.
- Binary files are rejected (Prusa `.bgcode` magic `GCDE` or detected NUL bytes).
- Rewrite is done via temp file and atomic replace.

If PrusaSlicer outputs `.bgcode`, disable Binary G-code and re-slice.

## Diagrams

All diagrams are in [`DIAGRAMS.md`](DIAGRAMS.md) under the **README Diagrams** section.

## Assumptions and Limits

- Intended for PrusaSlicer-style absolute XY toolpaths (`G90`).
- Skew angles are expected to be small.
- Z coordinates are not modified.
- Not a general-purpose G-code motion planner.

## When Not To Use

- Firmware already applies skew compensation (avoid double correction).
- Model geometry was already pre-corrected in CAD/mesh workflow.
- Measured skew is within measurement noise.

## Additional Docs

- Measurement guidance: [`MEASURING_SKEW.md`](MEASURING_SKEW.md)
- Design rationale: [`DESIGN.md`](DESIGN.md)

## License

MIT
