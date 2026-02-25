# Diagrams

Centralized Mermaid diagrams for `prusaslicer-skew-fix`.

## README Diagrams

### 1) End-to-end processing flow

```mermaid
flowchart TD
  A[".gcode from PrusaSlicer"] --> B["Reject binary; parse moves + modal state"]
  B --> C["Linearize G2/G3 arcs to G1 segments"]
  C --> D["Apply XY shear transform"]
  D --> E{"--recenter-to-bed?"}
  E -- "No" --> F["Write rewritten G-code"]
  E -- "Yes" --> G["Compute in-bed extruding bounds"]
  G --> H["Compute dx, dy translation"]
  H --> F
```

### 2) Recenter decision flow

```mermaid
flowchart TD
  A["Skewed in-bed extruding bounds"] --> B["Compute allowed dx, dy intervals"]
  B --> C{"Intervals valid?"}
  C -- "No" --> D["Error: cannot fit on bed"]
  C -- "Yes" --> E{"--recenter-mode"}
  E -- "center" --> F["dx, dy = interval midpoint"]
  E -- "clamp" --> G["dx, dy = min shift (0 if possible)"]
  F --> H["Apply dx, dy translation"]
  G --> H
```

## DESIGN Diagrams

### 1) Modal state machine (parsing)

```mermaid
stateDiagram-v2
  [*] --> ABS_XY
  ABS_XY --> REL_XY: "G91"
  REL_XY --> ABS_XY: "G90"

  state "Extrusion Mode" as EMode {
    [*] --> ABS_E
    ABS_E --> REL_E: "M83"
    REL_E --> ABS_E: "M82"
  }

  state "Arc Center Mode" as IJMode {
    [*] --> REL_IJ
    REL_IJ --> ABS_IJ: "G90.1"
    ABS_IJ --> REL_IJ: "G91.1"
  }
```

### 2) Arc linearization and E handling

```mermaid
flowchart TD
  A["G2/G3 arc with modal state"] --> B["Linearize to G1 points (0.20 mm / 5.0° max)"]
  B --> C["Apply shear to each point"]
  C --> D{"Extrusion mode"}
  D -- "absolute E (M82)" --> E["Emit cumulative E on each G1 segment"]
  D -- "relative E (M83)" --> F["Distribute arc delta-E across segments"]
  E --> G["Advance state to original arc endpoint"]
  F --> G
```

## MEASURING_SKEW Diagrams

### 1) Method selection

```mermaid
flowchart TD
  A["Need XY skew estimate"] --> B{"Califlower v2 available?"}
  B -- "Yes" --> C["--skew-deg\nor --skew-from-square / --skew-from-rectangle"]
  B -- "No" --> D{"Printed test shape?"}
  D -- "Square" --> E["--skew-from-square AC,BD,AD"]
  D -- "Rectangle" --> F["--skew-from-rectangle AC,BD,AD,AB"]
  D -- "Neither" --> G["Mechanical checks only\n(diagnostic, not geometry-based)"]
```
