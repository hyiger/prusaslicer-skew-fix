# Measuring XY Skew

Before applying XY skew correction, you need a reasonable estimate of the printer’s
XY non-orthogonality. This document describes **recommended ways to measure skew**,
from most accurate to most generic.

---

## Method 1 (Recommended): Califlower v2

**Califlower v2** is the most reliable and repeatable way to measure XY skew on a 3D printer.

Why this method is recommended:
- Designed specifically to isolate XY non-orthogonality
- Uses diagonal measurements that amplify small angular errors
- Produces results directly comparable to firmware skew correction (e.g. Marlin `M852`)
- Minimizes the influence of extrusion width, corner rounding, and slicer compensation

Typical workflow:
1. Print the Califlower v2 test object
2. Measure the indicated diagonals and sides using calipers
3. Use the Califlower analysis to compute the XY skew

Califlower reports skew either:
- directly as an **angle** (preferred, use `--skew-deg`), or
- as diagonal measurements that can be passed to `--skew-from-square` or `--skew-from-rectangle`

Example result:
```
XY skew = -0.15°
```

This value can be passed directly to the skew correction script:

```bash
--skew-deg -0.15
```

Or you can pass measurements and let the script derive the angle:

```bash
--skew-from-square AC,BD,AD
--skew-from-rectangle AC,BD,AD,AB
```

`--skew-from-rectangle` uses all four measurements: `AC`, `BD`, `AD`, and `AB`.

If you want confidence that you are correcting *geometry* rather than compensating for
extrusion artifacts, Califlower v2 is strongly recommended.

---

## Method 2: Square with diagonal measurement (generic)

This is a common, generic method that works with a simple printed square.

Procedure:
1. Print a **large square** (the larger the better; 100×100 mm or more is recommended)
2. Measure the X and Y side lengths (to verify scale)
3. Measure **both diagonals**

For a perfect square, both diagonals should be equal. A difference indicates XY skew.

Using the vertex labels from the diagram below (A = bottom-left, B = bottom-right, C = top-right, D = top-left):

- `AC` = diagonal from A to C (longer when skewed one way)
- `BD` = diagonal from B to D (shorter when skewed one way)
- `AD` = side from A to D (the measured side length)

The exact skew factor is:

`theta = arctan((AC² - BD²) / (4 · AD²))`

Notes:
- Accuracy depends heavily on caliper precision
- Corner rounding and elephant’s foot can distort results

This method is usable, but less robust than Califlower.

Script input form for this method:

```bash
--skew-from-square AC,BD,AD
```

---

## Method 3: Rectangle with diagonal measurement

A rectangle works the same way as the square method and can be useful when a
non-square print is more convenient or already available.

Procedure:
1. Print a rectangle (larger is better; at least 100 mm on the long axis)
2. Measure both diagonals and both sides with calipers

Using the vertex labels from the diagram below (A = bottom-left, B = bottom-right,
C = top-right, D = top-left):

- `AC` = diagonal from A to C
- `BD` = diagonal from B to D
- `AD` = side from A to D (height)
- `AB` = side from A to B (width)

The exact skew factor is:

`theta = arctan((AC² - BD²) / (4 · AB · AD))`

Limitations:
- Accuracy depends heavily on caliper precision
- Corner rounding and elephant's foot can distort results
- Larger and more square-like rectangles give better signal-to-noise

Script input form for this method:

```bash
--skew-from-rectangle AC,BD,AD,AB
```

---

## Method 4: Mechanical measurement (least recommended)

Mechanical approaches include:
- machinist squares
- dial indicators
- frame alignment measurements

While useful for diagnosing gross alignment issues, these methods:
- do not account for belt stretch or compliance
- do not reflect *printed* geometry
- are difficult to perform accurately on most consumer printers

Mechanical measurements should be treated as diagnostic tools only.

---

## Choosing a skew value

General guidance:
- Typical printers fall between **±0.05° and ±0.30°**
- Values outside this range often indicate a mechanical issue
- Over-correcting is worse than under-correcting

If uncertain:
- Prefer a **slightly smaller magnitude**
- Use `--analyze-only` to sanity-check the displacement before applying correction

---

## Practical notes

- XY skew primarily affects **X as a function of Y**
- Tall or deep parts benefit the most from correction
- Small parts may show little visible improvement

Always verify skew correction with:
- a dimensional test print
- or a before/after comparison

---

## Diagrams

All diagrams are in [`DIAGRAMS.md`](DIAGRAMS.md) under the **MEASURING_SKEW Diagrams** section.
The two geometry diagrams below show the actual vertex labels used in measurements.

### Square vertices and diagonals

```text
D o-------------------o C
  | \               / |
  |   \     BD    /   |
  |     \       /     |
  |       \   /       |
  |   AC    X         |
  |       /   \       |
  |     /       \     |
  |   /           \   |
  | /               \ |
A o-------------------o B
```

Measurements for `--skew-from-square AC,BD,AD`:
- `AC`: diagonal from `A` to `C`
- `BD`: diagonal from `B` to `D`
- `AD`: side from `A` to `D`

### Rectangle vertices and diagonals

```text
D o---------------------------o C
  | \                       / |
  |   \         BD        /   |
  |     \               /     |
  |       \           /       |
  |         \       /         |
  |   AC      \   /           |
  |             X             |
  |           /   \           |
  |         /       \         |
  |       /           \       |
  |     /               \     |
  |   /                   \   |
  | /                       \ |
A o---------------------------o B
```

Measurements for `--skew-from-rectangle AC,BD,AD,AB`:
- `AC`: diagonal from `A` to `C`
- `BD`: diagonal from `B` to `D`
- `AD`: side from `A` to `D`
- `AB`: side from `A` to `B`

---

## Summary

In order of preference:
1. **Califlower v2** — most accurate; reports angle directly
2. **Square diagonal measurement** — good general-purpose fallback
3. **Rectangle diagonal measurement** — same approach as square, use when a rectangle is more convenient
4. **Mechanical measurement** — diagnostic only; does not reflect printed geometry

Accurate skew measurement is the foundation of reliable skew correction.
