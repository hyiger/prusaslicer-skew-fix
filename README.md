# prusaslicer-skew-fix

**XY skew correction for PrusaSlicer when firmware M852 is unavailable.**

This repository provides a PrusaSlicer post-processing script that applies
XY skew (shear) compensation directly to generated **text** G-code.

Designed for Prusa printers using Buddy firmware (e.g. Prusa Core One),
where firmware-level skew correction (`M852`) is not supported.

## Skew Model

    x' = x + y * tan(theta)
    y' = y

`theta` is the measured XY skew angle in degrees (commonly from **Califlower V2**).

## PrusaSlicer Setup

In **Print Settings → Output options → Post-processing scripts**, add:

    python3 /path/to/skew_fix_ps.py --skew-deg -0.15

(Do **not** add `[output_filepath]` — PrusaSlicer supplies the path automatically.)

## IMPORTANT: PrusaConnect and Binary G-code

If you send jobs via **PrusaConnect**, PrusaSlicer may be configured to output **Binary G-code**
(often called `.bgcode`, with magic header `GCDE`). This script **cannot** edit binary files.

✅ Fix: **Disable “Binary G-code” output** in your printer/profile so PrusaSlicer produces a text `.gcode`.
Then re-slice.

This script includes a guard: if the file is binary, it aborts with a clear error instead of corrupting it.

## Verification

Look for this header in the output G-code:

    ; postprocess: prusaslicer-skew-fix --skew-deg -0.15

## License

MIT
