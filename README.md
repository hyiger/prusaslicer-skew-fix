# PrusaSlicer XY Skew Fix (Post-Processing Hook)

This repo contains a small **PrusaSlicer post-processing script** that applies **XY skew (shear) correction** directly to the generated G-code.

This is useful on printers/firmware that **do not support Marlin skew correction** (e.g. no `M852`), but you have a measured XY skew value (for example from **Califlower V2**).

## What it does

For each `G0`/`G1` move that includes X and/or Y, it applies:

- `x' = x + y * tan(θ)`
- `y' = y`

where `θ` is your measured XY skew angle (degrees).

The script:
- Tracks `G90/G91` (absolute/relative positioning) for XY moves
- Only modifies `G0/G1` X/Y coordinates
- Leaves Z/E/F and everything else untouched
- Rewrites the output file **in-place** using a temp file + atomic replace

## Quick start

1. Put `skew_fix_ps.py` somewhere stable on your machine.
2. Make sure Python 3 is installed.
3. In **PrusaSlicer** go to:

**Print Settings → Output options → Post-processing scripts**

Add a line:

### macOS / Linux

```bash
python3 /full/path/to/skew_fix_ps.py --skew-deg -0.15 -- "[output_filepath]"
```

### Windows

```bat
python "C:\full\path\to\skew_fix_ps.py" --skew-deg -0.15 -- "[output_filepath]"
```

> Keep the quotes around `"[output_filepath]"` so it works with spaces in paths.

## Verifying it ran

After slicing, open the G-code and look near the top for a line like:

```
; postprocess: skew_fix_ps.py --skew-deg -0.15
```

If you see it, the hook ran and the file was rewritten.

## Choosing the angle

Use the **XY skew angle** reported by your measurement tool (e.g. Califlower V2). For your earlier example:

- `-0.15°`

Set `--skew-deg -0.15`.

### Sign convention note

Different tools can define the sign differently. If a follow-up calibration print shows the skew got worse, flip the sign (use `+0.15`).

## Safety / limitations

- The script only changes `G0/G1` moves. It intentionally leaves arcs (`G2/G3`) untouched.
- This corrects a **single shear component** (X as a function of Y). That matches many XY-skew compensation models.
- Always keep your original G-code as a backup the first time you try this.

## License

MIT (you can add a LICENSE file if you want).
