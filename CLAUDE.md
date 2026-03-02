# CLAUDE.md — AI Assistant Guide for prusaslicer-skew-fix

This document captures the structure, conventions, and workflows of this repository for AI assistants.

---

## Project Overview

**prusaslicer-skew-fix** is a Python 3 post-processing script for PrusaSlicer. It applies XY skew correction to G-code files, compensating for non-orthogonality between the X and Y axes on 3D printers where firmware-level correction (`M852`) is unavailable (e.g. Prusa Buddy firmware / Core One).

The application-specific logic lives in `skew_fix_ps.py` (~460 lines). Generic G-code parsing, formatting, state tracking, arc linearization, and binary G-code handling are provided by `gcode_lib.py` (vendored from a separate library).

The correction is an affine shear transform matching Marlin's `M852` model:

```
x' = x + (y - y_ref) * tan(theta)
y' = y
```

Key behaviors:
- Arcs (`G2`/`G3`) are always linearized to `G1` segments because shear does not preserve circles.
- Both plain-text `.gcode` and Prusa binary `.bgcode` files are supported (including Heatshrink-compressed `.bgcode`).
- Binary `.bgcode` files are decoded, corrected, and re-encoded with all non-GCode blocks (thumbnails, metadata) preserved intact — suitable for direct upload to PrusaConnect.
- Bed bounds are auto-detected from `M862.3 P` printer model commands when not explicitly specified.
- File rewrites are atomic (temp file + replace).
- Recenter/bounds computation uses model-only extruding geometry, excluding purge/wipe/parking.

---

## Repository Structure

```
prusaslicer-skew-fix/
├── skew_fix_ps.py           # Application logic: CLI, bounds, recentering, analysis (~460 lines)
├── gcode_lib.py             # Vendored G-code library: parsing, formatting, transforms, bgcode I/O
├── tests/                   # pytest test suite
│   ├── conftest.py          # Root conftest: adds repo root to sys.path, load_module fixture
│   ├── test_analyze.py
│   ├── test_arc_center_modes.py
│   ├── test_arc_edge_cases.py
│   ├── test_arcs_contract.py
│   ├── test_basic.py
│   ├── test_bgcode.py       # Binary .bgcode read/write/roundtrip tests
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

## Source Files

### `gcode_lib.py` (vendored library)

Generic G-code library providing:
- **Parsing:** `GCodeLine`, `ModalState`, `parse_line`, `parse_words`, `split_comment`
- **State tracking:** `advance_state`, `iter_with_state`
- **Transforms:** `linearize_arcs`, `apply_skew`, `translate_xy`, `apply_xy_transform`
- **Bounds:** `compute_bounds`
- **I/O:** `load`/`save` (auto-detects text/bgcode), `_bgcode_split`/`_bgcode_reassemble`
- **Presets:** `PRINTER_PRESETS`, `detect_print_volume`, `detect_printer_preset`
- **Compression:** `COMP_NONE`, `COMP_DEFLATE`, Heatshrink (11/4 and 12/4)

Constants (`gcode_lib.EPS`, `gcode_lib.DEFAULT_XY_DECIMALS`, etc.) are authoritative; `skew_fix_ps.py` references them.

### `skew_fix_ps.py` (application)

Application-specific logic (~460 lines). There is no build step — it runs directly as `python3 skew_fix_ps.py`.

### Key functions in `skew_fix_ps.py`

| Function | Role |
|---|---|
| `compute_inbed_extruding_bounds_original(...)` | Computes XY bounds from in-bed extruding moves |
| `compute_translation_for_bounds(...)` | Derives (dx, dy) to keep geometry within bed+margin |
| `_choose_translation(lo, hi, mode)` | Picks center or clamp translation |
| `analyze_gcode(...)` | Reports skew effects without modifying the file |
| `rewrite(path, ...)` | Main pipeline: load → linearize → skew → translate → save |
| `skew_deg_from_square(ac, bd, ad)` | Derives skew angle from square diagonal measurements |
| `skew_deg_from_rectangle(ac, bd, ad, ab)` | Derives skew angle from rectangle measurements |
| `main()` | CLI entry point (`argparse`) |

### Binary G-code pipeline

Handled transparently by `gcode_lib.load()` and `gcode_lib.save()`:
1. `load()` auto-detects text vs bgcode, decompresses (DEFLATE/Heatshrink), returns `GCodeFile`.
2. `rewrite()` transforms the `GCodeFile.lines` list in-place.
3. `save()` re-encodes as bgcode (or text), atomically replaces the original file.

**Supported:** `COMP_NONE`, `COMP_DEFLATE`, Heatshrink 11/4 and 12/4; `ENC_RAW` (UTF-8).
**Unsupported:** MeatPack encoding (raises `SystemExit`).

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

1. **Two-file architecture** — application logic in `skew_fix_ps.py`, generic G-code operations in `gcode_lib.py` (vendored). Do not add other modules without strong reason.
2. **No external runtime dependencies** — only Python standard library at runtime. `gcode_lib.py` is vendored (not a pip dependency). `pytest` is dev-only.
3. **Python 3.10+ compatibility** — do not use syntax or stdlib features introduced after 3.10 without updating the CI matrix.
4. **Correctness-locked constants** — `ARC_SEG_MM`, `ARC_MAX_DEG`, `EPS` in `gcode_lib` control geometric accuracy. Changes require careful analysis of downstream effects on arc linearization and bounds calculations.
5. **Relative XY is unsupported** — the transform requires absolute coordinates; do not attempt to add relative-XY support without rethinking the entire state-tracking model.
6. **Z is never modified** — the transform is XY-only by design.
7. **Binary G-code block ordering** — per the libbgcode spec, GCode blocks must appear last in the file (after all metadata blocks). `gcode_lib._bgcode_reassemble` enforces this.

---

## Additional Documentation

| File | Contents |
|---|---|
| `README.md` | User guide, CLI reference, transform model, safety notes |
| `DESIGN.md` | Detailed rationale for every major design decision |
| `MEASURING_SKEW.md` | How to measure skew on a physical printer (4 methods) |
| `DIAGRAMS.md` | Mermaid flowcharts: processing flow, modal state machine, arc handling |
