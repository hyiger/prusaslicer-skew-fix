# CLAUDE.md — AI Assistant Guide for prusaslicer-skew-fix

This document captures the structure, conventions, and workflows of this repository for AI assistants.

---

## Project Overview

**prusaslicer-skew-fix** is a single-file Python 3 post-processing script for PrusaSlicer. It applies XY skew correction to text G-code files, compensating for non-orthogonality between the X and Y axes on 3D printers where firmware-level correction (`M852`) is unavailable (e.g. Prusa Buddy firmware / Core One).

The correction is an affine shear transform matching Marlin's `M852` model:

```
x' = x + (y - y_ref) * tan(theta)
y' = y
```

Key behaviors:
- Arcs (`G2`/`G3`) are always linearized to `G1` segments because shear does not preserve circles.
- Binary G-code is detected and rejected to prevent corruption.
- File rewrites are atomic (temp file + replace).
- Recenter/bounds computation uses model-only extruding geometry, excluding purge/wipe/parking.

---

## Repository Structure

```
prusaslicer-skew-fix/
├── skew_fix_ps.py           # Main application — the entire tool in one file (~900 lines)
├── tests/                   # pytest test suite (18 test modules)
│   ├── conftest.py          # Root conftest: adds repo root to sys.path, load_module fixture
│   ├── test_analyze.py
│   ├── test_arc_center_modes.py
│   ├── test_arc_edge_cases.py
│   ├── test_arcs_contract.py
│   ├── test_basic.py
│   ├── test_binary_guard.py
│   ├── test_bounds.py
│   ├── test_cli_integration.py
│   ├── test_cli_validation.py
│   ├── test_formatting.py
│   ├── test_helpers.py
│   ├── test_modal_parsing.py
│   ├── test_moves.py
│   ├── test_number_formats.py
│   ├── test_relative_xy_rejected.py
│   ├── test_rewrite.py
│   └── test_skew_from_measurements.py
├── .github/
│   └── workflows/
│       └── ci.yml           # GitHub Actions: pytest on Python 3.10–3.13
├── requirements-dev.txt     # Dev dependencies: pytest>=8.0 only
├── README.md                # User-facing documentation and CLI reference
├── DESIGN.md                # Design rationale (transform model, arc handling, bounds strategy)
├── DIAGRAMS.md              # Mermaid flowcharts for processing and state flows
├── MEASURING_SKEW.md        # How to measure printer skew (4 methods)
└── .gitignore
```

---

## Key Source File: `skew_fix_ps.py`

Everything lives in one file. There is no build step — it runs directly as `python3 skew_fix_ps.py`.

### Top-level constants (do not change without careful thought)

These are marked `# ---- Correctness-locked constants ----` in the source:

| Constant | Value | Purpose |
|---|---|---|
| `ARC_SEG_MM` | `0.20` | Max chord length (mm) per linearized arc segment |
| `ARC_MAX_DEG` | `5.0` | Max angle (degrees) per linearized arc segment |
| `EPS` | `1e-9` | Floating-point tolerance for comparisons |
| `XY_DECIMALS` | `3` | Default decimal places for X/Y output |
| `OTHER_DECIMALS` | `5` | Default decimal places for E/F/Z/other axes |

### Pre-compiled regexes (module-level)

```python
MOVE_RE   # matches G0/G1 commands
ARC_RE    # matches G2/G3 commands
AXIS_RE   # parses axis words like X12.345, Y-0.5, E1.2e-3
```

### Core data structure: `State` dataclass

Tracks G-code modal state during parsing:

```python
@dataclass
class State:
    abs_xy: bool = True       # G90 (abs) / G91 (rel) for X/Y
    abs_e: bool = True        # M82 (abs) / M83 (rel) for E
    ij_relative: bool = True  # G91.1 (rel IJ) / G90.1 (abs IJ)
    x: float = 0.0            # Current absolute X position
    y: float = 0.0            # Current absolute Y position
    z: float = 0.0
    e: float = 0.0
    f: Optional[float] = None
```

### Key functions

| Function | Role |
|---|---|
| `_handle_modal_state_line(st, up)` | Updates `State` for G90/G91/M82/M83/G90.1/G91.1; returns `True` if recognized |
| `split_comment(line)` | Splits a G-code line into `(code, comment)` at first `;` |
| `parse_words(code)` | Returns `Dict[str, float]` of axis words (X, Y, Z, E, F, I, J, K) |
| `apply_skew_abs(x, y, k, y_ref)` | Applies shear: `x' = x + (y - y_ref) * k` |
| `linearize_arc_points(...)` | Converts a G2/G3 arc into a list of `(x, y)` points |
| `fmt_axis(name, value, decimals)` | Formats a single axis word with trimmed trailing zeros |
| `replace_or_append(code, axis, value, decimals)` | Replaces or appends an axis word in a G-code command string |
| `compute_inbed_extruding_bounds_original(...)` | Computes XY bounds from in-bed extruding moves |
| `compute_translation_for_bounds(...)` | Derives (dx, dy) to keep geometry within bed+margin |
| `skew_deg_from_square(ac, bd, ad)` | Derives skew angle from square diagonal measurements |
| `skew_deg_from_rectangle(ac, bd, ad, ab)` | Derives skew angle from rectangle measurements |
| `_assert_text_gcode(path)` | Raises `SystemExit` if file appears to be binary G-code |
| `analyze_gcode(path, args)` | Reports skew effects without modifying the file |
| `rewrite(path, args)` | Main processing engine: parses, transforms, atomically rewrites |
| `main()` | CLI entry point (`argparse`) |

### Relative XY handling

Relative XY mode (`G91`) is **not supported for rewrite** — the script aborts with an error if a `G0`/`G1` move is encountered in relative mode during the rewrite pass. Relative IJ (arc center) mode is supported.

---

## Development Workflow

### Running tests

```bash
# Install dev dependencies (once)
pip install -r requirements-dev.txt

# Run the full test suite
pytest -q

# Run a specific test file
pytest tests/test_arcs_contract.py -v

# Run a specific test
pytest tests/test_moves.py::test_name -v
```

### Using the tool locally

```bash
# Analyze a file without modifying it
python3 skew_fix_ps.py --skew-deg -0.15 --shear-y-ref-mode auto --analyze-only /path/to/file.gcode

# Apply skew correction
python3 skew_fix_ps.py --skew-deg -0.15 --shear-y-ref-mode auto --recenter-to-bed --recenter-mode clamp /path/to/file.gcode
```

### Adding tests

- Place new test files in `tests/` named `test_<topic>.py`.
- Use the `load_module` fixture from `conftest.py` for tests that require a fresh module import (e.g. testing module-level state or `main()`).
- Use pytest's `tmp_path` fixture for any test that reads or writes files.
- Tests that exercise CLI argument validation should use `pytest.raises(SystemExit)`.

### No linting / formatting CI

There is no automated formatter or linter enforced in CI. Follow the existing code style: snake_case, type hints, docstrings on all public functions.

---

## CI/CD

GitHub Actions (`ci.yml`) runs on every push and pull request:

- **Matrix:** Python 3.10, 3.11, 3.12, 3.13 (fail-fast disabled)
- **Steps:** checkout → setup-python → `pip install -r requirements-dev.txt` → `pytest -q`

To match CI locally, use Python 3.10+ and run `pytest -q`.

---

## Code Conventions

### Style
- **Snake_case** for all functions and variables.
- **UPPERCASE** for module-level constants.
- **Type hints** throughout (uses `from __future__ import annotations` for forward compatibility with Python 3.10).
- **Docstrings** on all public functions.
- **Comments** explain non-obvious math/logic (arc geometry, bounds formulas).

### Error handling
- CLI errors use `sys.exit(msg)` / `parser.error(msg)` (raises `SystemExit`).
- Validation at function boundaries via assertions or early-return guards.
- No exception swallowing; failures surface loudly.

### Floating point
- Use `EPS = 1e-9` for comparisons instead of `==` on floats.
- Output precision: 3 decimals for XY, 5 for everything else (configurable via CLI).
- Trailing zeros are always trimmed from output.

### File safety
- Never overwrite input in-place directly — always write to a temp file first, then atomically replace.
- Always check for binary G-code before processing.

---

## Important Constraints

1. **Single-file architecture** — all logic lives in `skew_fix_ps.py`. Do not split into multiple modules unless there is a very strong reason.
2. **No external runtime dependencies** — only Python standard library at runtime. `pytest` is dev-only.
3. **Python 3.10+ compatibility** — do not use syntax or stdlib features introduced after 3.10 without updating the CI matrix.
4. **Correctness-locked constants** — `ARC_SEG_MM`, `ARC_MAX_DEG`, `EPS` control geometric accuracy. Changes require careful analysis of downstream effects on arc linearization and bounds calculations.
5. **Relative XY is unsupported** — the transform requires absolute coordinates; do not attempt to add relative-XY support without rethinking the entire state-tracking model.
6. **Z is never modified** — the transform is XY-only by design.

---

## Additional Documentation

| File | Contents |
|---|---|
| `README.md` | User guide, CLI reference, transform model, safety notes |
| `DESIGN.md` | Detailed rationale for every major design decision |
| `MEASURING_SKEW.md` | How to measure skew on a physical printer (4 methods) |
| `DIAGRAMS.md` | Mermaid flowcharts: processing flow, modal state machine, arc handling |
